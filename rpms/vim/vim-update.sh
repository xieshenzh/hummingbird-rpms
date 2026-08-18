#!/bin/bash
debug=""
#debug="echo "
branches=( "rawhide" "f45" "f44" "f43" )
releases=( "fc46" "fc45" "fc44" "fc43" )
# the first bodhi enabled release is the last without \| - all
# others need to have \|
regexps=( "fc46" "fc45" "fc44" "\|fc43" )
bodhi_enabled=( "0" "0" "1" "1" )
needs_update=()
#releases_regexp=fc28\\\|fc27\\\|fc28

branches_index=0
release_index=0
regexp_index=0
bodhi_enabled_index=0
done_build=0
releases_regexp=""
let "regexp_index+=1"

cd `dirname $0`
LANG=C
SPEC=vim.spec
CHANGES=1
force=0

if [ "x$1" == "x--force" ]; then
  force=1
fi

DATE=`date +"%a %b %d %Y"`
$debug fedpkg switch-branch "${branches[@]: $branches_index: 1}"


if [ $? -ne 0 ]; then
  echo "Error with switching branch"
  exit 1
fi

MAJORVERSION=`grep "define baseversion" vim.spec | cut -d ' ' -f 3`
MAJORVERDIR=$(echo $MAJORVERSION | sed -e 's/\.//')
EPOCH=`grep "Epoch:" vim.spec | cut -d ':' -f 2`
PACKAGER=`rpmdev-packager`
ORIGPL=`grep "define patchlevel" vim.spec | cut -d ' ' -f 3 | sed -e "s/^0*//g"`
ORIGPLFILLED=`printf "%03d" $ORIGPL`

if [ ! -d vim-upstream ]; then
   git clone https://github.com/vim/vim.git vim-upstream
else
   pushd vim-upstream
   git pull
   popd
fi

pushd vim-upstream

# get the latest tag. Might be tricky with other packages, but upstream vim uses just a single branch:
LASTTAG=$(git describe --tags $(git rev-list --tags --max-count=1))

# vim upstream tags have the form v7.4.123. Remove the 'v' and get major release and patchlevel:
UPSTREAMMAJOR=$(echo $LASTTAG | sed -e 's/v\([0-9]*\.[0-9]*\).*/\1/')
UPSTREAMMAJORDIR=$(echo $UPSTREAMMAJOR | sed -e 's/\.//')
LASTPL=`echo $LASTTAG| sed -e 's/.*\.//;s/^0*//'`
LASTPLFILLED=`printf "%03d" $LASTPL`
if [ $force -ne 1 -a "$ORIGPLFILLED" == "$LASTPLFILLED" ]; then
    echo "No new patchlevel available"
    CHANGES=0
fi
rm -rf dist/* 2>/dev/null
make unixall

# include patchlevel in tarball name so that older sources won't get overwritten:
mv dist/vim-${UPSTREAMMAJOR}.tar.bz2 dist/vim-${UPSTREAMMAJOR}-${LASTPLFILLED}.tar.bz2

# We don't include the full upstream changelog in the rpm changelog, just ship a file with
# the changes:
popd

cp -f vim-upstream/dist/vim-${UPSTREAMMAJOR}-${LASTPLFILLED}.tar.bz2 .
#wget https://raw.githubusercontent.com/ignatenkobrain/vim-spec-plugin/rawhide/ftplugin/spec.vim -O ftplugin-spec.vim
#wget https://raw.githubusercontent.com/ignatenkobrain/vim-spec-plugin/rawhide/syntax/spec.vim -O syntax-spec.vim
if [ $CHANGES -ne 0 ]; then
   CHLOG="* $DATE $PACKAGER -$EPOCH:$UPSTREAMMAJOR"
   $debug sed -i -e "/Release: /cRelease: 1%{?dist}" $SPEC
   if [ "x$MAJORVERSION" != "x$UPSTREAMMAJOR" ]; then
      $debug sed -i -s "s/define baseversion $MAJORVERSION/define baseversion $UPSTREAMMAJOR/" $SPEC
      $debug sed -i -s "s/define vimdir vim$MAJORVERDIR/define vimdir vim$UPSTREAMMAJORDIR/" $SPEC
   fi
   $debug sed -i -e "s/define patchlevel $ORIGPLFILLED/define patchlevel $LASTPLFILLED/" $SPEC
   $debug sed -i -e "/\%changelog/a$CHLOG.$LASTPLFILLED-1\n- patchlevel $LASTPLFILLED\n" $SPEC
   $debug fedpkg new-sources vim-${UPSTREAMMAJOR}-${LASTPLFILLED}.tar.bz2
   $debug git add vim.spec
   $debug git commit -m "- patchlevel $LASTPL" 

   # mockbuild
   $debug fedpkg mockbuild
   if [ $? -ne 0 ]; then
     echo "Error: fedpkg mockbuild"
     exit 1
   fi

   # push
   $debug fedpkg push
   if [ $? -ne 0 ]; then
     echo "Error: fedpkg push"
     exit 1
   fi

   $debug fedpkg build
   if [ $? -eq 0 ]; then
     done_build=1
   else
     echo "Error when building package in $branch"
     exit 1
   fi

   let "release_index+=1"
   let "bodhi_enabled_index+=1"

   for branch in "${branches[@]:(1)}";
   do
     # switch to branch
     $debug fedpkg switch-branch $branch

     # merge with previous branch
     $debug bash -c "git merge rawhide <<<':x'"
     if [ $? -ne 0 ]; then
       echo "Error: git merge rawhide"
       exit 1
     fi

     # mockbuild
     $debug fedpkg mockbuild
     if [ $? -ne 0 ]; then
       echo "Error: fedpkg mockbuild failed"
       exit 1
     fi

     # push
     $debug fedpkg push
     if [ $? -ne 0 ]; then
       echo "Error: fedpkg push"
       exit 1
     fi

     # append next release to regexp if the branch is enabled in bodhi - because we
     # need to check if there aren't any testing updates from higher enabled branches
     # (lower branch cannot have bigger NVR than higher branch) in next iteration
     if [ ${bodhi_enabled[@]: $bodhi_enabled_index: 1} -eq 1 ]
     then
       releases_regexp="$releases_regexp${regexps[@]: regexp_index: 1}"
     fi

     # Check if release has an update in testing - if not, build package
     # and submit update for testing
     # done_build is a check, if previous branch did build - lower branch can do
     # a build only when higher branch build was ok.
     testing_update=`bodhi updates query --packages vim --status testing \
       | grep $releases_regexp`
     if [ "$testing_update" == "" ] && [ $done_build -eq 1 ]; then
       $debug fedpkg build
       if [ $? -eq 0 ]; then
         # if branch isn't rawhide or branch is enabled in bodhi, create the update if newer branch does
         # not have an update in testing
         if [ $branch != "rawhide" ] || [ ${bodhi_enabled[@]: $bodhi_enabled_index: 1} -eq 1 ]; then
           $debug bodhi updates new --type enhancement --notes "The newest upstream commit" --request testing --autotime --autokarma --stable-karma 3 --unstable-karma -3 vim-${UPSTREAMMAJOR}.${LASTPLFILLED}-1.${releases[@]: $release_index: 1}
         fi
       else
         echo "Error when building package for $branch"
         exit 1
       fi
     else
       done_build=0
     fi

     # Increment index
     let "branches_index+=1"
     let "release_index+=1"
     let "regexp_index+=1"
     let "bodhi_enabled_index+=1"
   done
   #$debug git push
   #if [ $? -eq 0 ]; then
   #   $debug rm -f $HOME/.koji/config
   #   $debug fedpkg build
   #   $debug ln -sf ppc-config $HOME/.koji/config
   #else
   #   echo "GIT push failed"
   #fi
fi

#go back to rawhide
$debug fedpkg switch-branch rawhide

# clean up the downloaded vim-upstream repo - to prevent changes in it and breaking update process
# prevents #1931099
$debug rm -rf vim-upstream

exit 0
