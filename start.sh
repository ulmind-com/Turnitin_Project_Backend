#!/bin/bash
set -e

# Default to "web" process type if not set
PROCESS_TYPE=${PROCESS_TYPE:-web}

echo "Starting container in $PROCESS_TYPE mode..."

if [ "$PROCESS_TYPE" = "web" ]; then
    # Start the FastAPI web application using uvicorn
    # Use environment port if defined, fallback to 8000
    PORT_NUM=${PORT:-8000}
    echo "Running FastAPI web server on port $PORT_NUM..."
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT_NUM" --workers 4
elif [ "$PROCESS_TYPE" = "worker" ]; then
    # In earlier versions, ARQ background worker was used. 
    # Since background tasks were refactored to native FastAPI BackgroundTasks inside the web container, 
    # running a separate worker process is deprecated.
    echo "=========================================================================="
    echo "WARNING: ARQ Worker process type requested."
    echo "NOTE: Background tasks have been refactored to native FastAPI BackgroundTasks."
    echo "All processing now runs inside the Web server process. No separate worker is needed."
    echo "This container will sleep to prevent crash-loop-backoffs if deployed as worker."
    echo "=========================================================================="
    while true; do sleep 3600; done
else
    echo "ERROR: Unknown PROCESS_TYPE '$PROCESS_TYPE'."
    exit 1
fi
