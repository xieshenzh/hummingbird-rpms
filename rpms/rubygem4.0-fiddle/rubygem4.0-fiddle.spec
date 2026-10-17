Name:           rubygem4.0-fiddle
Version:        1.1.8
Release:        1%{?dist}
Summary:        Fiddle (libffi wrapper) for the ruby4.0 stream

License:        BSD-2-Clause
URL:            https://github.com/ruby/fiddle
Source0:        https://rubygems.org/gems/fiddle-1.1.8.gem

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  ruby4.0-devel
BuildRequires:  libffi-devel
Requires:       ruby(rubygems)

%description
Fiddle, the Ruby libffi wrapper (foreign function interface), packaged for
the Hummingbird ruby4.0 stream. Ruby 4.0 removed fiddle from the default
gems and the Hummingbird repo does not carry it, but the gz-tools Ruby CLI
(`gz`) requires it (SharedLibInterface dlopens the gz command libraries) —
so the ROS 2 simulation image needs it as an explicit dependency. Layout
mirrors rubygem4.0-psych: the compiled .so under /usr/lib64/gems/ruby/
plus the stdlib copy under /usr/lib64/ruby/, gem files under
/usr/share/gems/.

%prep
%setup -q -T -c -n fiddle-%{version}
tar xf %{SOURCE0} data.tar.gz
tar xf data.tar.gz
rm -f data.tar.gz

%build
cd ext/fiddle
ruby extconf.rb
%make_build

%install
GEMDIR=%{buildroot}/usr/share/gems/gems/fiddle-%{version}
mkdir -p "$GEMDIR" %{buildroot}/usr/share/gems/specifications
cp -r lib "$GEMDIR"/
# the .gem's own fiddle.gemspec is a BUILD-TIME spec (evaluates version.rb
# relative to itself) — rubygems rejects it at activation. Emit the proper
# installed-form spec from the gem metadata instead:
gem spec %{SOURCE0} --ruby \
    > %{buildroot}/usr/share/gems/specifications/fiddle-%{version}.gemspec
EXTDIR=%{buildroot}/usr/lib64/gems/ruby/fiddle-%{version}
mkdir -p "$EXTDIR" %{buildroot}/usr/lib64/ruby
install -m 0755 ext/fiddle/fiddle.so "$EXTDIR"/
touch "$EXTDIR"/gem.build_complete
install -m 0755 ext/fiddle/fiddle.so %{buildroot}/usr/lib64/ruby/fiddle.so

%files
%license LICENSE.txt
/usr/share/gems/gems/fiddle-%{version}
/usr/share/gems/specifications/fiddle-%{version}.gemspec
/usr/lib64/gems/ruby/fiddle-%{version}
/usr/lib64/ruby/fiddle.so

%changelog
%autochangelog
