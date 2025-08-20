#!/bin/bash
cd /home/kavia/workspace/code-generation/student-attendance-tracker-8814/attendance_backend_service
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

