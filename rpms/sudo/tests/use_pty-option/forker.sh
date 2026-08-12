#!/bin/bash
for i in `seq 1 10`; do
    ( ping -c 10 -q www.redhat.com & )
done

