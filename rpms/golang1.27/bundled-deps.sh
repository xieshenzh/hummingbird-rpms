#!/bin/bash
# Copyright (C) 2021 Jakub Čajka jcajka@redhat.com
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

provides=""

for bundle in $(find -name modules.txt); do
	provides="$provides\n$(cat "$bundle" |
		grep "^# " |
		grep -v "# explicit" |
		sed -r s/"^#.* => "// |
		sed -r "s/# //" |
		sed -r "s:(.*) v(.*):Provides\: bundled(golang(\1)) = \2:" |
		sed '/= .*/s/-/./g')"
	done
echo -e "$provides" | sed 's/-/./g' | sort -u
