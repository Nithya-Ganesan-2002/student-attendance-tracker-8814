import os
import csv
import io
from datetime import date, datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, status, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, EmailStr
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Supabase configuration via ENV (do NOT hardcode)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Since we cannot guarantee external libs beyond requirements, we will use httpx for Supabase REST calls and JWT validation via Supabase
# but stub detailed verification for now while keeping interfaces ready.
import httpx

# Security: HTTP Bearer Token (JWT from Supabase)
bearer_scheme = HTTPBearer(auto_error=False)

# Role constants
ROLE_ADMIN = "admin"
ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"
ALL_ROLES = {ROLE_ADMIN, ROLE_TEACHER, ROLE_STUDENT}

# In-memory storage for demo purposes (replace with DB integration in future iterations).
# These structures are keyed and shaped for clarity; they are placeholders for Supabase/Postgres tables.
DB = {
    "users": {},          # user_id -> {id, email, name, role}
    "classes": {},        # class_id -> {id, name, description, teacher_id, students: [user_ids]}
    "attendance": [],     # list of {id, class_id, student_id, date, status, marked_by}
    "notifications": [],  # list of {id, user_id, message, created_at, type, read}
}

def _gen_id(prefix: str) -> str:
    return f"{prefix}_{int(datetime.utcnow().timestamp() * 1000)}"

# PUBLIC_INTERFACE
class AppInfo(BaseModel):
    """Application metadata for OpenAPI and health."""
    name: str = Field(..., description="Service name")
    version: str = Field(..., description="Service version")
    time: datetime = Field(..., description="Current server time (UTC)")

# PUBLIC_INTERFACE
class LoginRequest(BaseModel):
    """Email/password login request body."""
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., description="User password")

# PUBLIC_INTERFACE
class LoginResponse(BaseModel):
    """Token response from Supabase login."""
    access_token: str = Field(..., description="JWT access token issued by Supabase")
    token_type: str = Field(default="bearer", description="Token type")
    user_id: str = Field(..., description="Supabase auth user id")

# PUBLIC_INTERFACE
class UserCreate(BaseModel):
    """Create user body."""
    email: EmailStr = Field(..., description="User email")
    name: str = Field(..., description="Full name")
    role: str = Field(..., description="Role: admin|teacher|student")

# PUBLIC_INTERFACE
class UserUpdate(BaseModel):
    """Update user body."""
    name: Optional[str] = Field(None, description="Full name")
    role: Optional[str] = Field(None, description="Role: admin|teacher|student")

# PUBLIC_INTERFACE
class UserOut(BaseModel):
    """User response model."""
    id: str = Field(..., description="User id")
    email: EmailStr = Field(..., description="Email")
    name: str = Field(..., description="Full name")
    role: str = Field(..., description="Role")

# PUBLIC_INTERFACE
class ClassCreate(BaseModel):
    """Create class body."""
    name: str = Field(..., description="Class name")
    description: Optional[str] = Field("", description="Class description")
    teacher_id: str = Field(..., description="Teacher user id")
    student_ids: List[str] = Field(default_factory=list, description="Initial student ids")

# PUBLIC_INTERFACE
class ClassUpdate(BaseModel):
    """Update class body."""
    name: Optional[str] = Field(None, description="Class name")
    description: Optional[str] = Field(None, description="Class description")
    teacher_id: Optional[str] = Field(None, description="Teacher user id")
    student_ids: Optional[List[str]] = Field(None, description="Student ids to set")

# PUBLIC_INTERFACE
class ClassOut(BaseModel):
    """Class response model."""
    id: str = Field(..., description="Class id")
    name: str = Field(..., description="Class name")
    description: Optional[str] = Field("", description="Class description")
    teacher_id: str = Field(..., description="Teacher user id")
    student_ids: List[str] = Field(default_factory=list, description="Student ids")

# PUBLIC_INTERFACE
class AttendanceMark(BaseModel):
    """Attendance mark request."""
    class_id: str = Field(..., description="Class id")
    student_id: str = Field(..., description="Student id")
    date: date = Field(default_factory=date.today, description="Attendance date")
    status: str = Field(..., description="Status: present|absent|late|excused")

# PUBLIC_INTERFACE
class AttendanceQuery(BaseModel):
    """Attendance query filters."""
    class_id: Optional[str] = Field(None, description="Class id")
    student_id: Optional[str] = Field(None, description="Student id")
    start_date: Optional[date] = Field(None, description="Start date")
    end_date: Optional[date] = Field(None, description="End date")

# PUBLIC_INTERFACE
class AttendanceOut(BaseModel):
    """Attendance record response."""
    id: str = Field(..., description="Record id")
    class_id: str = Field(..., description="Class id")
    student_id: str = Field(..., description="Student id")
    date: date = Field(..., description="Date")
    status: str = Field(..., description="Status")
    marked_by: str = Field(..., description="Marker user id")

# PUBLIC_INTERFACE
class ReportRequest(BaseModel):
    """Report request filters."""
    class_id: Optional[str] = Field(None, description="Class id")
    student_id: Optional[str] = Field(None, description="Student id")
    start_date: Optional[date] = Field(None, description="Start date")
    end_date: Optional[date] = Field(None, description="End date")
    format: str = Field("csv", description="csv or pdf")

# PUBLIC_INTERFACE
class NotificationCreate(BaseModel):
    """Create notification request."""
    user_id: str = Field(..., description="Target user id")
    message: str = Field(..., description="Notification message")
    type: str = Field("low_attendance", description="Type of notification")

# PUBLIC_INTERFACE
class NotificationOut(BaseModel):
    """Notification response."""
    id: str = Field(..., description="Notification id")
    user_id: str = Field(..., description="Target user id")
    message: str = Field(..., description="Notification message")
    created_at: datetime = Field(..., description="Creation time (UTC)")
    type: str = Field(..., description="Type")
    read: bool = Field(False, description="Read flag")

def require_env():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase environment variables SUPABASE_URL and SUPABASE_KEY must be set."
        )

# PUBLIC_INTERFACE
async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Dict[str, Any]:
    """Validate Supabase JWT via Supabase auth endpoint and return user info.
    This function expects Authorization: Bearer <token>.
    """
    if credentials is None or not credentials.scheme.lower() == "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    token = credentials.credentials

    # For initial version, we will call Supabase auth v1 to get user from token (if possible).
    # If network blocked or Supabase not reachable, we gracefully fallback to a demo user role.
    require_env()
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{SUPABASE_URL}/auth/v1/user", headers=headers)
            if resp.status_code == 200:
                user_data = resp.json()
                user_id = user_data.get("id") or user_data.get("user", {}).get("id") or "user_unknown"
                email = user_data.get("email") or user_data.get("user", {}).get("email") or "unknown@example.com"
                # Map to local user with role; if not exists, default to student.
                local = DB["users"].get(user_id)
                if not local:
                    local = {"id": user_id, "email": email, "name": email.split("@")[0], "role": ROLE_STUDENT}
                    DB["users"][user_id] = local
                return local
            else:
                # Fallback (unauthorized)
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except httpx.HTTPError:
        # In offline/demo mode allow a stub teacher to continue development workflows.
        demo_user = {"id": "demo_teacher", "email": "demo@local", "name": "Demo Teacher", "role": ROLE_TEACHER}
        DB["users"][demo_user["id"]] = demo_user
        return demo_user

def require_roles(allowed: List[str]):
    def checker(user: Dict[str, Any] = Depends(get_current_user)):
        if user.get("role") not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: insufficient role")
        return user
    return checker

app = FastAPI(
    title="Attendance Backend Service",
    description="APIs for authentication, attendance management, role-based user/class management, reports, notifications, and export.",
    version="0.1.0",
    openapi_tags=[
        {"name": "Health", "description": "Service health and metadata"},
        {"name": "Auth", "description": "Authentication with Supabase"},
        {"name": "Users", "description": "User management (admin)"},
        {"name": "Classes", "description": "Class management (admin/teacher)"},
        {"name": "Attendance", "description": "Attendance marking and tracking"},
        {"name": "Reports", "description": "Reports and exports"},
        {"name": "Notifications", "description": "Notification dispatch and listing"},
        {"name": "Realtime", "description": "WebSocket usage notes"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For dev; restrict in production.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health and metadata
@app.get("/", response_model=Dict[str, str], tags=["Health"], summary="Health Check", description="Simple health check endpoint.")
def health_check():
    return {"message": "Healthy"}

# PUBLIC_INTERFACE
@app.get("/info", response_model=AppInfo, tags=["Health"], summary="Service Info", description="Provides service metadata and server time.")
def service_info():
    """Service info including name, version, and current time."""
    return AppInfo(name="attendance_backend_service", version="0.1.0", time=datetime.utcnow())

# Auth endpoints
# PUBLIC_INTERFACE
@app.post("/auth/login", response_model=LoginResponse, tags=["Auth"], summary="Supabase email/password login",
          description="Authenticate against Supabase using email/password and return access token.")
async def login(payload: LoginRequest = Body(...)):
    """Authenticate user using Supabase email/password sign-in."""
    require_env()
    data = {"email": payload.email, "password": payload.password}
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{SUPABASE_URL}/auth/v1/token?grant_type=password", headers=headers, json=data)
        if resp.status_code != 200:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        body = resp.json()
        access_token = body.get("access_token")
        user_id = body.get("user", {}).get("id") if isinstance(body.get("user"), dict) else body.get("user_id")
        if not access_token or not user_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid response from auth provider")
        # Ensure local user shadow exists (defaults to student)
        if user_id not in DB["users"]:
            DB["users"][user_id] = {"id": user_id, "email": payload.email, "name": payload.email.split("@")[0], "role": ROLE_STUDENT}
        return LoginResponse(access_token=access_token, token_type="bearer", user_id=user_id)

# Users management (admin)
# PUBLIC_INTERFACE
@app.post("/users", response_model=UserOut, tags=["Users"], summary="Create user (admin)", description="Create a user record and role assignment in local store (shadow of Supabase user).")
def create_user(user: UserCreate, current=Depends(require_roles([ROLE_ADMIN]))):
    """Create a new user in the local store. Assumes user exists in Supabase auth."""
    if user.role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    user_id = _gen_id("usr")
    record = {"id": user_id, "email": user.email, "name": user.name, "role": user.role}
    DB["users"][user_id] = record
    return record

# PUBLIC_INTERFACE
@app.get("/users", response_model=List[UserOut], tags=["Users"], summary="List users (admin)", description="List all users with roles.")
def list_users(current=Depends(require_roles([ROLE_ADMIN]))):
    return list(DB["users"].values())

# PUBLIC_INTERFACE
@app.get("/users/{user_id}", response_model=UserOut, tags=["Users"], summary="Get user (admin)", description="Fetch a user by id.")
def get_user(user_id: str, current=Depends(require_roles([ROLE_ADMIN]))):
    user = DB["users"].get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# PUBLIC_INTERFACE
@app.put("/users/{user_id}", response_model=UserOut, tags=["Users"], summary="Update user (admin)", description="Update user fields and role.")
def update_user(user_id: str, payload: UserUpdate, current=Depends(require_roles([ROLE_ADMIN]))):
    user = DB["users"].get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.role and payload.role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if payload.name is not None:
        user["name"] = payload.name
    if payload.role is not None:
        user["role"] = payload.role
    return user

# PUBLIC_INTERFACE
@app.delete("/users/{user_id}", status_code=204, tags=["Users"], summary="Delete user (admin)", description="Delete user by id.")
def delete_user(user_id: str, current=Depends(require_roles([ROLE_ADMIN]))):
    if user_id in DB["users"]:
        del DB["users"][user_id]
    return JSONResponse(status_code=204, content=None)

# Classes management
# PUBLIC_INTERFACE
@app.post("/classes", response_model=ClassOut, tags=["Classes"], summary="Create class (admin/teacher)", description="Create a new class and assign teacher and students.")
def create_class(payload: ClassCreate, current=Depends(require_roles([ROLE_ADMIN, ROLE_TEACHER]))):
    if payload.teacher_id not in DB["users"]:
        raise HTTPException(status_code=400, detail="Teacher not found")
    cid = _gen_id("cls")
    record = {
        "id": cid,
        "name": payload.name,
        "description": payload.description or "",
        "teacher_id": payload.teacher_id,
        "student_ids": payload.student_ids or [],
    }
    DB["classes"][cid] = record
    return record

# PUBLIC_INTERFACE
@app.get("/classes", response_model=List[ClassOut], tags=["Classes"], summary="List classes", description="List classes visible to current user.")
def list_classes(current=Depends(get_current_user)):
    if current["role"] == ROLE_ADMIN:
        return list(DB["classes"].values())
    if current["role"] == ROLE_TEACHER:
        return [c for c in DB["classes"].values() if c["teacher_id"] == current["id"]]
    # student
    return [c for c in DB["classes"].values() if current["id"] in c.get("student_ids", [])]

# PUBLIC_INTERFACE
@app.get("/classes/{class_id}", response_model=ClassOut, tags=["Classes"], summary="Get class", description="Get class details.")
def get_class(class_id: str, current=Depends(get_current_user)):
    c = DB["classes"].get(class_id)
    if not c:
        raise HTTPException(status_code=404, detail="Class not found")
    # Access control
    if current["role"] == ROLE_ADMIN:
        return c
    if current["role"] == ROLE_TEACHER and c["teacher_id"] == current["id"]:
        return c
    if current["role"] == ROLE_STUDENT and current["id"] in c.get("student_ids", []):
        return c
    raise HTTPException(status_code=403, detail="Forbidden")

# PUBLIC_INTERFACE
@app.put("/classes/{class_id}", response_model=ClassOut, tags=["Classes"], summary="Update class (admin/teacher)", description="Update class attributes.")
def update_class(class_id: str, payload: ClassUpdate, current=Depends(require_roles([ROLE_ADMIN, ROLE_TEACHER]))):
    c = DB["classes"].get(class_id)
    if not c:
        raise HTTPException(status_code=404, detail="Class not found")
    if current["role"] == ROLE_TEACHER and c["teacher_id"] != current["id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    if payload.name is not None:
        c["name"] = payload.name
    if payload.description is not None:
        c["description"] = payload.description
    if payload.teacher_id is not None:
        c["teacher_id"] = payload.teacher_id
    if payload.student_ids is not None:
        c["student_ids"] = payload.student_ids
    return c

# PUBLIC_INTERFACE
@app.delete("/classes/{class_id}", status_code=204, tags=["Classes"], summary="Delete class (admin/teacher)", description="Delete a class.")
def delete_class(class_id: str, current=Depends(require_roles([ROLE_ADMIN, ROLE_TEACHER]))):
    c = DB["classes"].get(class_id)
    if not c:
        return JSONResponse(status_code=204, content=None)
    if current["role"] == ROLE_TEACHER and c["teacher_id"] != current["id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    del DB["classes"][class_id]
    return JSONResponse(status_code=204, content=None)

# Attendance
def _attendance_filter(rec: Dict[str, Any], q: AttendanceQuery) -> bool:
    ok = True
    if q.class_id:
        ok = ok and (rec["class_id"] == q.class_id)
    if q.student_id:
        ok = ok and (rec["student_id"] == q.student_id)
    if q.start_date:
        ok = ok and (rec["date"] >= q.start_date)
    if q.end_date:
        ok = ok and (rec["date"] <= q.end_date)
    return ok

# PUBLIC_INTERFACE
@app.post("/attendance/mark", response_model=AttendanceOut, tags=["Attendance"], summary="Mark attendance (teacher/admin)",
          description="Mark student attendance for a given class and date.")
def mark_attendance(payload: AttendanceMark, current=Depends(require_roles([ROLE_ADMIN, ROLE_TEACHER]))):
    c = DB["classes"].get(payload.class_id)
    if not c:
        raise HTTPException(status_code=404, detail="Class not found")
    if current["role"] == ROLE_TEACHER and c["teacher_id"] != current["id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    if payload.student_id not in c.get("student_ids", []):
        raise HTTPException(status_code=400, detail="Student not in class")
    rec_id = _gen_id("att")
    record = {
        "id": rec_id,
        "class_id": payload.class_id,
        "student_id": payload.student_id,
        "date": payload.date,
        "status": payload.status,
        "marked_by": current["id"],
    }
    DB["attendance"].append(record)
    return record

# PUBLIC_INTERFACE
@app.get("/attendance", response_model=List[AttendanceOut], tags=["Attendance"], summary="Query attendance", description="Query attendance by class, student, and date range.")
def query_attendance(
    class_id: Optional[str] = Query(None),
    student_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current=Depends(get_current_user),
):
    q = AttendanceQuery(class_id=class_id, student_id=student_id, start_date=start_date, end_date=end_date)
    # Access control: teachers can see their classes, students see their own, admins see all
    def visible(rec: Dict[str, Any]) -> bool:
        if current["role"] == ROLE_ADMIN:
            return True
        c = DB["classes"].get(rec["class_id"])
        if not c:
            return False
        if current["role"] == ROLE_TEACHER:
            return c["teacher_id"] == current["id"]
        if current["role"] == ROLE_STUDENT:
            return rec["student_id"] == current["id"]
        return False
    return [r for r in DB["attendance"] if _attendance_filter(r, q) and visible(r)]

# Reports and exports
def _build_report_rows(q: AttendanceQuery) -> List[Dict[str, Any]]:
    rows = [r for r in DB["attendance"] if _attendance_filter(r, q)]
    # enrich
    out = []
    for r in rows:
        student = DB["users"].get(r["student_id"], {"name": r["student_id"]})
        clazz = DB["classes"].get(r["class_id"], {"name": r["class_id"]})
        out.append({
            "date": r["date"].isoformat() if isinstance(r["date"], date) else str(r["date"]),
            "class_id": r["class_id"],
            "class_name": clazz.get("name", ""),
            "student_id": r["student_id"],
            "student_name": student.get("name", ""),
            "status": r["status"],
            "marked_by": r["marked_by"],
        })
    return out

def _rows_to_csv(rows: List[Dict[str, Any]]) -> bytes:
    if not rows:
        return b"date,class_id,class_name,student_id,student_name,status,marked_by\n"
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")

def _rows_to_pdf_stub(rows: List[Dict[str, Any]]) -> bytes:
    # For now, create a very simple PDF-like text; in future integrate reportlab or WeasyPrint.
    content = "Attendance Report (PDF stub)\n\n"
    for row in rows:
        content += f"{row['date']} | {row['class_name']} | {row['student_name']} | {row['status']}\n"
    # Return as application/pdf though it's a stub; downstream can be updated once real PDF generation lib is approved.
    return content.encode("utf-8")

# PUBLIC_INTERFACE
@app.post("/reports/export", tags=["Reports"], summary="Export attendance report", description="Export attendance report in CSV or PDF format.")
def export_report(req: ReportRequest, current=Depends(require_roles([ROLE_ADMIN, ROLE_TEACHER]))):
    q = AttendanceQuery(
        class_id=req.class_id,
        student_id=req.student_id,
        start_date=req.start_date,
        end_date=req.end_date,
    )
    rows = _build_report_rows(q)
    if req.format.lower() == "csv":
        data = _rows_to_csv(rows)
        return StreamingResponse(io.BytesIO(data), media_type="text/csv", headers={
            "Content-Disposition": "attachment; filename=attendance.csv"
        })
    elif req.format.lower() == "pdf":
        data = _rows_to_pdf_stub(rows)
        return StreamingResponse(io.BytesIO(data), media_type="application/pdf", headers={
            "Content-Disposition": "attachment; filename=attendance.pdf"
        })
    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Use csv or pdf.")

# Notifications (stub for real-time alerts)
def _create_notification(user_id: str, message: str, ntype: str = "low_attendance") -> Dict[str, Any]:
    notif = {
        "id": _gen_id("ntf"),
        "user_id": user_id,
        "message": message,
        "created_at": datetime.utcnow(),
        "type": ntype,
        "read": False,
    }
    DB["notifications"].append(notif)
    return notif

# PUBLIC_INTERFACE
@app.post("/notifications", response_model=NotificationOut, tags=["Notifications"], summary="Create notification (admin/teacher)",
          description="Create a notification for a user. Real-time push is stubbed for now.")
def create_notification(req: NotificationCreate, current=Depends(require_roles([ROLE_ADMIN, ROLE_TEACHER]))):
    notif = _create_notification(req.user_id, req.message, req.type)
    return notif

# PUBLIC_INTERFACE
@app.get("/notifications", response_model=List[NotificationOut], tags=["Notifications"], summary="List my notifications",
         description="List notifications for the current user.")
def list_my_notifications(current=Depends(get_current_user)):
    return [n for n in DB["notifications"] if n["user_id"] == current["id"]]

# PUBLIC_INTERFACE
@app.post("/notifications/{notif_id}/read", response_model=NotificationOut, tags=["Notifications"], summary="Mark notification as read",
          description="Mark a specific notification as read.")
def mark_notification_read(notif_id: str, current=Depends(get_current_user)):
    for n in DB["notifications"]:
        if n["id"] == notif_id and n["user_id"] == current["id"]:
            n["read"] = True
            return n
    raise HTTPException(status_code=404, detail="Notification not found")

# Realtime/WebSocket usage info
# PUBLIC_INTERFACE
@app.get("/docs/realtime", tags=["Realtime"], summary="WebSocket Usage", description="Explains the real-time WebSocket endpoint for notifications.")
def websocket_usage():
    """Describe how to connect to the notifications WebSocket (to be implemented in future iterations)."""
    return {
        "websocket_endpoint": "/ws/notifications",
        "notes": "Connect with Authorization: Bearer <token> in query or header. Receive real-time notifications when available.",
        "status": "stubbed"
    }
