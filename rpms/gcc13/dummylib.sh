#!/bin/sh
if [ $# -lt 4 ]; then echo Usage: dummylib.sh orig_lib_path dummy_lib_path mapfile new_lib_path; exit 1; fi
TMPDIR=`mktemp -d dummylib.sh.XXXXXX` || exit 1
F=`file -L $1`
C=
S=8
X=@
case "$F" in
  *ELF\ 64-bit*shared\ object*x86-64*) C=-m64;;
  *ELF\ 32-bit*shared\ object*80?86*) C=-m32; S=4;;
  *ELF\ 64-bit*shared\ object*PowerPC*) C=-m64;;
  *ELF\ 32-bit*shared\ object*PowerPC*) C=-m32; S=4;;
  *ELF\ 64-bit*shared\ object*cisco*) C=-m64;;
  *ELF\ 32-bit*shared\ object*cisco*) C=-m32; S=4;;
  *ELF\ 64-bit*shared\ object*IA-64*) C=;;
  *ELF\ 64-bit*shared\ object*Alpha*) C=;;
  *ELF\ 64-bit*shared\ object*390*) C=-m64;;
  *ELF\ 32-bit*shared\ object*390*) C=-m31; S=4;;
  *ELF\ 64-bit*shared\ object*SPARC*) C=-m64;;
  *ELF\ 32-bit*shared\ object*SPARC*) C=-m32; S=4;;
  *ELF\ 64-bit*shared\ object*Alpha*) C=;;
  *ELF\ 32-bit*shared\ object*ARM*) C=; S=4; X=%;;
  *ELF\ 64-bit*shared\ object*ARM\ aarch64*) C=; X=%;;
esac
cp $3 $TMPDIR/lib.map
( readelf -Ws $1; echo @@@@@@END@@@@@@; readelf -Ws $4 ) | sed 's/ \[[^]]*\] //g' | awk '
/@@@@@@END@@@@@@/ { start=0; new=1 }
/\.dynsym.* contains/ { start=1 }
/^$/ { start=0 }
/  UND / { next }
/@/ { if (start) {
  fn=$8
  if (new)
    {
      if (seen[fn])
	next
      fn=gensub(/@@/,"@",1,fn)
      ver=gensub(/^.*@/,"",1,fn)
      sym=gensub(/@.*$/,"",1,fn)
      syms[ver]=syms[ver] "    " sym ";\n"
    }
  else
    seen[fn]=1
  intfn="HACK" hack+0
  hack++
  if ($4 ~ /FUNC/) { print ".text"; size=16; print ".type " intfn ",@function" }
  else if ($4 ~ /OBJECT/ && $5 ~ /UNIQUE/) { print ".data"; print ".balign 16"; size=$3; print ".type " intfn ",@gnu_unique_object" }
  else if ($4 ~ /OBJECT/) { print ".data"; print ".balign 16"; size=$3; print ".type " intfn ",@object" }
  else if ($4 ~ /TLS/ && $5 ~ /UNIQUE/) { print ".section .tdata,\"awT\",@progbits"; print ".balign 16"; size=$3; print ".type " intfn ",@gnu_unique_object" }
  else if ($4 ~ /TLS/) { print ".section .tdata,\"awT\",@progbits"; print ".balign 16"; size=$3; print ".type " intfn ",@object" }
  else if ($4 ~ /NOTYPE/) { print ".data"; print ".balign 16"; size=$3 }
  else exit(1);
  print ".globl " intfn
  if ($5 ~ /WEAK/) { print ".weak " intfn }
  else if ($5 !~ /GLOBAL/ && $5 !~ /UNIQUE/) exit(1);
  print intfn ": .skip " size
  print ".size " intfn "," size
  print ".symver " intfn "," fn
  if ($5 ~ /UNIQUE/) print ".type \"" fn "\",@gnu_unique_object"
} }
END {
  mapfile="'$TMPDIR/lib.map'"
  for (ver in syms) {
    printf "%s {\n%s};\n", ver, syms[ver] >> mapfile
  }
}
' > $TMPDIR/lib.s || exit
if [ "$X" = "%" ]; then
  sed -i -e 's/@\(function\|gnu_unique_object\|object\|progbits\)/%\1/g' $TMPDIR/lib.s
fi
soname=`readelf -Wd $1 | grep SONAME | sed 's/^.*\[//;s/\].*$//'`
gcc $C -shared -Wa,--noexecstack -Wl,-soname,$soname,-version-script,$TMPDIR/lib.map \
    -o $2 $TMPDIR/lib.s -nostdlib
strip $2
rm -rf $TMPDIR
