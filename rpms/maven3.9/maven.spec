Name:           maven3.9
Version:        3.9.16
Release:        0.2%{?dist}
Summary:        Java project management and comprehension tool

%global homedir %{_datadir}/maven
%global confdir %{_sysconfdir}/maven

# Maven itself is Apache-2.0. The remaining terms cover the libraries
# copied from Maven Central into the assembled Maven distribution.
License:        Apache-2.0 AND BSD-3-Clause AND EPL-2.0 AND MIT AND LicenseRef-Fedora-Public-Domain AND (CDDL-1.0 OR GPL-2.0-only WITH Classpath-exception-2.0)
URL:            https://maven.apache.org/
Source0:        https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/%{version}/apache-maven-%{version}-src.tar.gz
# Source1 contains the Maven Central artifacts needed for a network-free build.
# Run generate-vendor.sh to reproduce it.
Source1:        maven-%{version}-repository.tar.zst

BuildArch:      noarch
BuildRequires:  java-25-openjdk-devel
BuildRequires:  maven
BuildRequires:  tar
BuildRequires:  zstd
Requires:       java-headless >= 1:1.8
Requires:       bash
Requires:       maven3.9-lib = %{version}-%{release}
Provides:       maven = %{version}-%{release}

%description
Apache Maven command-line tools for Maven 3.9.


%package lib
Summary:        Core libraries for Maven 3.9

# Third-party libraries shipped in lib/ and boot/. Maven modules built by
# this package are not listed as bundled dependencies.
# BEGIN generated bundled Maven Provides
Provides: bundled(mvn(aopalliance:aopalliance)) = 1.0
Provides: bundled(mvn(com.google.code.gson:gson)) = 2.13.2
Provides: bundled(mvn(com.google.errorprone:error_prone_annotations)) = 2.41.0
Provides: bundled(mvn(com.google.guava:failureaccess)) = 1.0.3
Provides: bundled(mvn(com.google.guava:guava)) = 33.6.0-jre
Provides: bundled(mvn(com.google.inject:guice)) = 5.1.0
Provides: bundled(mvn(commons-cli:commons-cli)) = 1.11.0
Provides: bundled(mvn(commons-codec:commons-codec)) = 1.21.0
Provides: bundled(mvn(javax.annotation:javax.annotation-api)) = 1.3.2
Provides: bundled(mvn(javax.inject:javax.inject)) = 1
Provides: bundled(mvn(org.apache.httpcomponents:httpclient)) = 4.5.14
Provides: bundled(mvn(org.apache.httpcomponents:httpcore)) = 4.4.16
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-api)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-connector-basic)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-impl)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-named-locks)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-spi)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-transport-file)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-transport-http)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-transport-wagon)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.resolver:maven-resolver-util)) = 1.9.27
Provides: bundled(mvn(org.apache.maven.shared:maven-shared-utils)) = 3.4.2
Provides: bundled(mvn(org.apache.maven.wagon:wagon-file)) = 3.5.3
Provides: bundled(mvn(org.apache.maven.wagon:wagon-http)) = 3.5.3
Provides: bundled(mvn(org.apache.maven.wagon:wagon-http-shared)) = 3.5.3
Provides: bundled(mvn(org.apache.maven.wagon:wagon-provider-api)) = 3.5.3
Provides: bundled(mvn(org.codehaus.plexus:plexus-cipher)) = 2.0
Provides: bundled(mvn(org.codehaus.plexus:plexus-classworlds)) = 2.11.0
Provides: bundled(mvn(org.codehaus.plexus:plexus-component-annotations)) = 2.2.0
Provides: bundled(mvn(org.codehaus.plexus:plexus-interpolation)) = 1.29
Provides: bundled(mvn(org.codehaus.plexus:plexus-sec-dispatcher)) = 2.0
Provides: bundled(mvn(org.codehaus.plexus:plexus-utils)) = 3.6.1
Provides: bundled(mvn(org.eclipse.sisu:org.eclipse.sisu.inject)) = 1.0.0
Provides: bundled(mvn(org.eclipse.sisu:org.eclipse.sisu.plexus)) = 1.0.0
Provides: bundled(mvn(org.fusesource.jansi:jansi)) = 2.4.3
Provides: bundled(mvn(org.jspecify:jspecify)) = 1.0.0
Provides: bundled(mvn(org.ow2.asm:asm)) = 9.9.1
Provides: bundled(mvn(org.slf4j:jcl-over-slf4j)) = 1.7.36
Provides: bundled(mvn(org.slf4j:slf4j-api)) = 1.7.36
# END generated bundled Maven Provides

%description lib
Core libraries and the private runtime distribution for Maven 3.9, arranged
using Fedora's Maven layout for Hummingbird builds.


%prep
%setup -q -n apache-maven-%{version}
mkdir ../repository
tar --zstd -xf %{SOURCE1} -C ../repository


%build
mvn \
    --batch-mode \
    --no-transfer-progress \
    --offline \
    -Dmaven.repo.local="$PWD/../repository" \
    clean package


%install
mkdir -p %{buildroot}%{homedir} %{buildroot}%{_bindir}
tar -xzf apache-maven/target/apache-maven-%{version}-bin.tar.gz \
    --strip-components=1 \
    -C %{buildroot}%{homedir}

# The upstream archive contains Windows DLLs from Jansi. Maven uses the pure
# Java fallback on Linux, so do not turn this noarch RPM into a binary bundle.
find %{buildroot}%{homedir}/lib/jansi-native -type f \
    ! -name README.txt -delete
find %{buildroot}%{homedir}/lib/jansi-native -type d -empty -delete

install -d %{buildroot}%{confdir}
mv %{buildroot}%{homedir}/bin/m2.conf \
    %{buildroot}%{_sysconfdir}/m2.conf
ln -s %{_sysconfdir}/m2.conf \
    %{buildroot}%{homedir}/bin/m2.conf
mv %{buildroot}%{homedir}/conf/settings.xml \
    %{buildroot}%{confdir}/settings.xml
mv %{buildroot}%{homedir}/conf/logging \
    %{buildroot}%{confdir}/logging
ln -s %{confdir}/settings.xml %{buildroot}%{homedir}/conf/settings.xml
ln -s %{confdir}/logging %{buildroot}%{homedir}/conf/logging

ln -s %{homedir}/bin/mvn %{buildroot}%{_bindir}/mvn
ln -s %{homedir}/bin/mvnDebug %{buildroot}%{_bindir}/mvnDebug


%check
mv %{buildroot}%{homedir}/conf/logging \
    %{buildroot}%{homedir}/conf/logging.rpm-link
cp -a %{buildroot}%{confdir}/logging \
    %{buildroot}%{homedir}/conf/logging
ln -sfn %{buildroot}%{_sysconfdir}/m2.conf \
    %{buildroot}%{homedir}/bin/m2.conf
MAVEN_USER_HOME="$PWD/test-home" \
    %{buildroot}%{homedir}/bin/mvn --version
rm -rf %{buildroot}%{homedir}/conf/logging
mv %{buildroot}%{homedir}/conf/logging.rpm-link \
    %{buildroot}%{homedir}/conf/logging
ln -sfn %{_sysconfdir}/m2.conf \
    %{buildroot}%{homedir}/bin/m2.conf


%files lib
%license LICENSE NOTICE
%doc README.md
%{homedir}
%exclude %{homedir}/bin/mvn*
%config(noreplace) %{_sysconfdir}/m2.conf
%config(noreplace) %{confdir}/settings.xml
%config(noreplace) %{confdir}/logging/simplelogger.properties

%files
%{homedir}/bin/mvn*
%{_bindir}/mvn
%{_bindir}/mvnDebug


%changelog
%autochangelog
