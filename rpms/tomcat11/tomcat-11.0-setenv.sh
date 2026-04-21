#!/bin/sh
#

export CATALINA_HOME=/usr/share/tomcat

# Try to find Java installation and set JAVA_HOME
/bin/sh /usr/libexec/tomcat/tomcat-locate-java.sh

# Default Java options
if [ -z "$JAVA_OPTS" ]; then
	JAVA_OPTS="-Djava.awt.headless=true"
fi
