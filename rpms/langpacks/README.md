# Fedora langpacks package

This package enables langpacks for glibc, hunspell dictionaries, KDE,
and other packages, as well has default language support
(fonts and input methods) for languages.

## Meta-package structure

```
langpacks-* -> langpacks-{core,fonts}-* -> default-fonts-*
```

### Default fonts

```
default-fonts -> default-fonts-{core,cjk,other}-* -> default-fonts-*
```

```
default-fonts-core-* = default-core-{sans,mono,serif,emoji,math}
default-fonts-cjk-* = default-fonts-cjk-{sans,mono,serif}
default-fonts-other-* = default-fonts-other-{sans,mono,serif}
```

## FAQ

### [Q] How to remove a default font or langpack meta-package?

Because of the chains of meta-packages it can be a little daunting to remove
default fonts for example (ie without inducing dnf to remove lots of
default packages).

But it can be done easily with for example:

```
sudo dnf remove google-noto-sans-<LANG>-fonts --noautoremove
```
or
```
sudo dnf remove default-fonts-LANGCODE --noautoremove
```

Though it is recommended to use the default-fonts in general.
