#!/bin/sh

ip netns del cs_client >/dev/null 2>&1
ip link del veth0 >/dev/null 2>&1

ip netns add cs_client
ip link add type veth
ip link set veth1 name eth1 netns cs_client

export cs_client_if1=eth1
export cs_server_if1=veth0
export cs_client_ip1=2001:db8:ffff::1
export cs_server_ip1=2001:db8:ffff::2

ip netns exec cs_client ip link set $cs_client_if1 up
ip link set $cs_server_if1 up
ip netns exec cs_client ip -6 addr add $cs_client_ip1/64 dev $cs_client_if1
ip -6 addr add $cs_server_ip1/64 dev $cs_server_if1
ip netns exec cs_client ifconfig lo up
ifconfig lo up
