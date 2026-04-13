if [ -z "$TRIVY_CONFIG" ] && [ -f /etc/trivy/trivy.yaml ]; then
    export TRIVY_CONFIG=/etc/trivy/trivy.yaml
fi
