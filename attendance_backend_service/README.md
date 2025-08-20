# Attendance Backend Service – Supabase Setup

This service requires Supabase credentials provided via environment variables. Create a `.env` from `.env.example` and set:

- SUPABASE_URL
- SUPABASE_KEY (use the service_role for server-side operations; keep it secret)

Notes:
- The API validates JWTs by calling `${SUPABASE_URL}/auth/v1/user` with the provided bearer token and SUPABASE_KEY.
- Ensure the frontend obtains a Supabase session (via @supabase/supabase-js) and passes the access token as `Authorization: Bearer <token>`.

Development:
- python -m uvicorn src.api.main:app --reload
- By default, the in-memory DB is used for domain logic. Supabase Postgres tables and RLS policies can be provisioned as described in `assets/supabase.md`.
