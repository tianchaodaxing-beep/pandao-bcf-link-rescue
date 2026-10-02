# Third-party notices

## IfcOpenShell 0.9.0

- Source: https://github.com/IfcOpenShell/IfcOpenShell/tree/v0.9.0
- Distribution: https://pypi.org/project/ifcopenshell/0.9.0/
- License: GNU Lesser General Public License, version 3 or later.
- Copyright: the IfcOpenShell contributors, including Thomas Krijnen.
- Official license texts are included in `third-party/IfcOpenShell-COPYING` and `third-party/IfcOpenShell-COPYING.LESSER`.
- Used as a separately installed dependency, without modification. No compiled IfcOpenShell binary is included in this repository. If distributing an environment containing it, preserve its upstream notices and applicable LGPL source/relinking obligations.

## fflate 0.8.3

- Source and license: https://github.com/101arrowz/fflate
- Package: https://registry.npmjs.org/fflate/-/fflate-0.8.3.tgz
- License: MIT, copyright Arjun Barrett.
- Vendored unmodified UMD build: `docs/vendor/fflate-0.8.3.js`.
- License text: `docs/vendor/LICENSE-fflate`.
- Original package SHA-256: `38c2cd824402407b43153c782274aec2ea83ea688e4aa0b743c5f2c305857d92`.

## Optional test dependencies

`bcf-client` 0.9.0 is an IfcOpenShell project BCF library, licensed LGPL-3.0-or-later. It is installed only for optional import validation and is not bundled. `pytest` is MIT licensed. `lxml` uses a BSD license and its underlying libxml2/libxslt licenses; it is optional for independently obtained official XSD checks. Test dependencies retain their upstream licenses.

## BCF specification

The application implements relevant BCF 2.1 XML structures. Reference: https://github.com/buildingSMART/BCF-XML/tree/release_2_1 . This repository does not redistribute official schemas or claim buildingSMART certification.
