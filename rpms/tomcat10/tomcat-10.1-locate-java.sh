#!/bin/sh
# Script looking for a Java runtime suitable for running Tomcat.
#The Java runtime found is exported in the JAVA_HOME environment variable.
set -e
if [ -z "$JAVA_HOME" ]; then
  INSTALLATION_PATH="/usr/lib/jvm/"
  for jvmdir in "${INSTALLATION_PATH}"java* "${INSTALLATION_PATH}"jre*; do
    if [ -d "${jvmdir}" ] && [ -r "${jvmdir}/bin/java" ]; then
      MAJOR_JAVA_VERSION=$("${jvmdir}/bin/java" --version | head -n 1 | sed -E 's/^[^0-9]*1\.([0-9]+).*/\1/; t; s/^[^0-9]*([0-9]+).*/\1/')
      # Tomcat 10 requires Java >= 11
      if [ "${MAJOR_JAVA_VERSION}" -ge 11 ]; then
        export JAVA_HOME="${jvmdir}"
        exit 0
      fi
    fi
  done
fi
if [ -z "$JAVA_HOME" ]; then
    echo "No JDK or JRE found - Please set the JAVA_HOME variable."
    exit 1
fi
