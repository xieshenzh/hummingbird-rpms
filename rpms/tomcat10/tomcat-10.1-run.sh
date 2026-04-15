#!/bin/sh
# Startup script for Apache Tomcat with systemd

set -e

# Load the service settings
. /etc/tomcat/tomcat.conf

# Try to find Java installation and set JAVA_HOME
/bin/sh  /usr/libexec/tomcat/tomcat-locate-java.sh

# Enable the Java security manager?
SECURITY=""
[ "$SECURITY_MANAGER" = "true" ] && SECURITY="-security"


# Start Tomcat
cd "$CATALINA_BASE" && "$CATALINA_HOME"/bin/catalina.sh run $SECURITY