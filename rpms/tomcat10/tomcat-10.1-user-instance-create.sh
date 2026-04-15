#!/bin/sh
# Script to create a CATALINA_BASE directory for your own tomcat

PROG=$(basename "$0")
CATALINA_HOME="/usr/share/tomcat"
TARGET=""
HPORT=8080
CPORT=8005
CWORD="SHUTDOWN"
warned=0
warnlowport=0

usage() {
  echo "Usage: $PROG [options] <directoryname>"
  echo "  directoryname: name of the tomcat instance directory to create"
  echo "Options:"
  echo "  -h, --help         Display this help message"
  echo "  -p <HTTPPORT>      HTTP port to be used by Tomcat (default is $HPORT)"
  echo "  -c <CONTROLPORT>   Server shutdown control port (default is $CPORT)"
  echo "  -w <MAGICWORD>     Word to send to trigger shutdown (default is $CWORD)"
}

checkport() {
  type=$1
  port=$2
  # Fail if port is non-numeric
  case "$port" in
      ''|*[!0-9]*)
          echo "Error: ${type} port '${port}' is not a valid TCP port number."
          exit 1
          ;;
  esac

  num=$((port + 1)) 2>/dev/null || {
      echo "Error: ${type} port '${port}' is not a valid TCP port number."
      exit 1
  }

  if [ "$num" -lt 2 ] || [ "$num" -gt 65536 ]; then
    echo "Error: ${type} port '${port}' is not a valid TCP port number or is above TCP port numbers (> 65535)."
    exit 1
  fi

  # Warn if port is below 1024 (once)
  if [ ${warnlowport} -eq 0 ]; then 
    if [ "${port}" -lt 1024 ]; then
      echo "Warning: ports below 1024 are reserved to the super-user."
      warnlowport=1
      warned=1
    fi
  fi

  # Warn if port appears to be in use
  if ss -tln | grep -q ":${port} "; then
	  echo "Warning: ${type} port ${port} appears to be in use."
	  warned=1
  fi
}

if [ "$#" -lt 1 ]; then
  usage
  exit 1
fi
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  usage
  exit 0
fi

while getopts ":p:c:w:h" options; do
  case $options in
    p ) HPORT=$OPTARG ;;
    c ) CPORT=$OPTARG ;;
    w ) CWORD=$OPTARG ;;
    h ) usage;;
    \? ) echo "Error: Unknown parameter '-$OPTARG'."
        exit 1;;
  esac
done

shift $((OPTIND - 1))
TARGET=$1
shift
echo "You are about to create a Tomcat instance in directory '$TARGET'"

# Fail if no target specified
if [ -z "${TARGET}" ]; then
  echo "Error: No target directory specified (use -d)."
  exit 1
fi

# Fail if ports are the same
if [ "${HPORT}" = "${CPORT}" ]; then
  echo "Error: HTTP port and control port must be different."
  exit 1
fi

# Fail if target directory already exists
if [ -d "${TARGET}" ]; then
  echo "Error: Target directory already exists."
  exit 1
fi

# Check ports
checkport HTTP "${HPORT}"
checkport Control "${CPORT}"

# Ask for confirmation if warnings were printed out
if [ ${warned} -eq 1 ]; then 
  echo "Press <ENTER> to continue, or type 'no' to abort."
  read -r answer
  case "$answer" in
      [nN][oO]|[nN])
        echo "Aborting."
        exit 1
        ;;
    esac
fi

mkdir -p "${TARGET}"

FULLTARGET=$(cd "${TARGET}" > /dev/null && pwd)

mkdir "${TARGET}/logs"
mkdir "${TARGET}/webapps"
mkdir "${TARGET}/work"
mkdir "${TARGET}/temp"
cp -r ${CATALINA_HOME}/user-instance/* "${TARGET}"

sed -i -e "s/Connector port=\"8080\"/Connector port=\"${HPORT}\"/;s/Server port=\"-1\" shutdown=\"SHUTDOWN\"/Server port=\"${CPORT}\" shutdown=\"${CWORD}\"/" "${TARGET}/conf/server.xml"

cat > "${TARGET}/bin/startup.sh" << EOF
#!/bin/sh
export CATALINA_BASE="${FULLTARGET}"
"${CATALINA_HOME}"/bin/startup.sh
echo "Tomcat started"
EOF

cat > "${TARGET}/bin/shutdown.sh" << EOF
#!/bin/sh
export CATALINA_BASE="${FULLTARGET}"
"${CATALINA_HOME}"/bin/shutdown.sh
echo "Tomcat stopped"
EOF

chmod a+x "${TARGET}/bin/startup.sh" "${TARGET}/bin/shutdown.sh"
echo "* New Tomcat instance created in ${TARGET}"
echo "* You might want to edit default configuration in ${TARGET}/conf"
echo "* Run ${TARGET}/bin/startup.sh to start your Tomcat instance"
