#!/bin/sh

export VHOST1_NAME="vhost1"
export VHOST1_ADDR="192.168.100.101"
export VHOST1_PORT="80"

export VHOST2_NAME="vhost2"
export VHOST2_ADDR="192.168.100.102"
export VHOST2_PORT="80"

mkdir -p /var/www/html/${VHOST1_NAME}
mkdir -p /var/www/html/${VHOST2_NAME}

echo ${VHOST1_NAME} > /var/www/html/${VHOST1_NAME}/index.html
echo ${VHOST2_NAME} > /var/www/html/${VHOST2_NAME}/index.html

cat >/etc/httpd/conf.d/vhost.conf <<EOF
<VirtualHost ${VHOST1_ADDR}:${VHOST1_PORT}>
    DocumentRoot /var/www/html/${VHOST1_NAME}
</VirtualHost>
<VirtualHost ${VHOST2_ADDR}:${VHOST2_PORT}>
    DocumentRoot /var/www/html/${VHOST2_NAME}
</VirtualHost>
EOF

echo -ne "[debug]: adding ${VHOST1_ADDR} ... "
ip addr add ${VHOST1_ADDR} dev lo
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: adding ${VHOST1_ADDR} ... "
ip addr add ${VHOST2_ADDR} dev lo
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: starting httpd service ... "
systemctl start httpd
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: checking httpd active ... "
systemctl -q is-active httpd
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

VHOST1_VARS='${VHOST1_NAME}:${VHOST1_ADDR}:${VHOST1_PORT}'
VHOST2_VARS='${VHOST2_NAME}:${VHOST2_ADDR}:${VHOST2_PORT}'

echo -ne "[debug]: setting up config file .... "
envsubst "${VHOST1_VARS},${VHOST2_VARS}" < ./haproxy.cfg.in > /etc/haproxy/haproxy.cfg
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: configuring selinux policy ... "
setsebool haproxy_connect_any 1
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: starting haproxy service ... "
systemctl start haproxy
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: checking haproxy active ... "
systemctl -q is-active haproxy
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: fetching URL via haproxy ... "
if [[ $( curl -s http://127.0.0.1:81 ) != ${VHOST1_NAME} ]] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: fetching URL via haproxy ... "
if [[ $( curl -s http://127.0.0.1:81 ) != ${VHOST2_NAME} ]] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: stopping haproxy service ... "
systemctl stop haproxy
if [ $? -ne 0 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

echo -ne "[debug]: checking haproxy inactive ... "
systemctl -q is-active haproxy
if [ $? -ne 3 ] ; then
    echo "FAIL"
    exit 1
else
    echo "OK"
fi

systemctl stop httpd

ip addr del ${VHOST1_ADDR}/32 dev lo
ip addr del ${VHOST2_ADDR}/32 dev lo

exit 0
