#!/usr/bin/python3 -Wall
# -*- coding: utf-8 -*-

# Generate /etc/services from IANA registry
# Ville Skyttä <ville.skytta@iki.fi> 2016

import re
from urllib.request import urlopen
from xml.etree import ElementTree as ET

url = "http://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xml"
ns = {"iana": "http://www.iana.org/assignments"}

with urlopen(url) as stream:
    tree = ET.parse(stream)

print("# service-name  port/protocol  [aliases ...]   [# comment]")

for record in tree.getroot().findall("iana:record", ns):

    name = record.find("iana:name", ns)
    if name is None or not name.text or not re.match(r"[A-Za-z0-9-]+$", name.text):
        continue

    protocol = record.find("iana:protocol", ns)
    if protocol is None or not protocol.text:
        continue

    description = record.find("iana:description", ns).text
    if description and description.lower() == name.text.lower():
        description = None

    number = record.find("iana:number", ns)
    if number is None or not number.text:
        continue
    if "-" in number.text:
        start, end = map(int, number.text.split("-"))
        number_range = range(start, end + 1)
    else:
        number_range = [number.text]

    for number in number_range:
        numproto = "%s/%s" % (number, protocol.text)

        row = "%-16s" % name.text
        if description:
            row += "%-32s# %s" % (numproto, " ".join(description.split()))
        else:
            row += numproto
        print(row)
