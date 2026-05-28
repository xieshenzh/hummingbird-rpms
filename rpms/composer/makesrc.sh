#!/bin/bash

NAME=$(sed    -n '/^Name:/{s/.* //;p}'                    *.spec)
OWNER=$(sed   -n '/^%global gh_owner/{s/.* //;p}'         $NAME.spec)
PROJECT=$(sed -n '/^%global gh_project/{s/.* //;p}'       $NAME.spec)
VERSION=$(sed -n '/^%global upstream_version/{s/.* //;p}' $NAME.spec)
PREVER=$(sed  -n '/^%global upstream_prever/{s/.* //;p}'  $NAME.spec)
if [ -z "$PREVER" ]
then TAG=$VERSION
else TAG=$VERSION-$PREVER
fi

if [ -f $NAME-$TAG.tgz -a "$1" != "-f" ]; then
	echo skip $NAME-$TAG.tgz already here
else
	echo -e "\nCreate git snapshot\nName=$NAME, Owner=$OWNER, Project=$PROJECT, Version=$TAG\n"

	echo "Cloning..."
	git clone https://github.com/$OWNER/$PROJECT.git --depth 1 --branch $TAG $PROJECT-$TAG

	echo "Getting TAG $TAG..."
	pushd $PROJECT-$TAG
		cp composer.json ../composer.json
		composer config platform.php 7.2.5
		rm composer.lock
		composer install --no-interaction --no-progress --no-dev --optimize-autoloader
		cp vendor/composer/installed.json ../

		echo "Bash completion"
		bin/composer completion bash >../composer-bash-completion
	popd

	echo "Archiving..."
	tar czf $NAME-$TAG.tgz --exclude-vcs $PROJECT-$TAG

	echo "Cleaning..."
	rm -rf $PROJECT-$TAG
fi
echo "Done."
