#!/bin/bash
# Wait for Ollama to become responsive before starting dependent services
echo "Waiting for Ollama..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "Ollama is ready."
        exit 0
    fi
    sleep 1
done
echo "ERROR: Ollama did not start within 30 seconds."
exit 1
