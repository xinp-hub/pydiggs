# Licence and use

pyDIGGS's own code is free and open-source software under the
[Apache License 2.0](https://github.com/xinp-hub/pydiggs/blob/master/LICENSE): you can use
it for any purpose, including commercial work and closed-source products, at no cost and
without asking.

To validate offline, pyDIGGS also bundles reference data that it did not write: the DIGGS
schemas and dictionaries and, inside them, schemas and unit definitions from
Energistics, OGC and the EPSG Dataset. That material keeps its own terms, and some of
them are not open-source licences (see [Third-party material](#third-party-material-inside-pydiggs)).
They allow ordinary use of pyDIGGS, and the notices below are what they ask for.

This page explains what that means in practice. It is not legal advice; the licence
texts decide.

## What you can do

- **Use it.** Run pyDIGGS on your own projects, your clients' projects and in production
  systems.
- **Build on it.** Call it from your own software, open or closed, free or paid.
- **Change it.** Modify it and use or distribute your modified version.
- **Ship it.** Include pyDIGGS in an application, container image, installer or service
  you sell or give away.

The licence also gives you a patent licence from every contributor for their
contributions. That licence ends for anyone who sues claiming that pyDIGGS infringes a
patent.

## What you must do when you redistribute it

Using pyDIGGS's own code, including running it as a service, carries no conditions. If
you **distribute** pyDIGGS, in source form or built into your product, you must:

1. Include a copy of the [licence](https://github.com/xinp-hub/pydiggs/blob/master/LICENSE).
2. Include the attribution notices in the
   [`NOTICE`](https://github.com/xinp-hub/pydiggs/blob/master/NOTICE) file, in your own
   `NOTICE` file, your documentation, or wherever your product shows third-party notices
   (an About box, for example).
3. Keep existing copyright notices, and mark any files you changed.

The wheel on PyPI carries `LICENSE`, `NOTICE` and the third-party licence texts in its
`.dist-info/licenses/` folder, and its licence metadata names every licence involved:

```text
Apache-2.0 AND MPL-2.0 AND MIT AND LicenseRef-Energistics-PLA AND LicenseRef-POSC-LA
AND LicenseRef-OGC-2001 AND LicenseRef-EPSG-TOU AND LicenseRef-DIGGS-Unstated
AND LicenseRef-OASIS
```

A tool that builds a software bill of materials (SBOM) or a third-party notices page
finds all of this automatically. The `LicenseRef-` entries are terms that are not on the
SPDX list; their texts are in
[`LICENSES/`](https://github.com/xinp-hub/pydiggs/tree/master/LICENSES).
`LicenseRef-OASIS` covers a test file in the source distribution only.

### Checklist for products that include pyDIGGS

If you ship pyDIGGS inside an application, appliance, container image or installer:

- [ ] Your third-party notices include the pyDIGGS `LICENSE` and `NOTICE` (the `NOTICE`
      already carries the Energistics, OGC and EPSG attributions those terms ask for).
- [ ] Your SBOM lists pyDIGGS with its version and full licence expression, as above.
- [ ] Your licence review has looked at the non-SPDX terms: the
      [Energistics Product License Agreement](https://energistics.org/product-license-agreement)
      and the [EPSG Dataset Terms of Use](https://epsg.org/terms-of-use.html). The EPSG
      terms allow the data in commercial products as long as you charge for your own
      product, not for the EPSG data.
- [ ] You do not use the DIGGS, Energistics, OGC or IOGP names to suggest endorsement.

## Third-party material inside pyDIGGS

pyDIGGS bundles the DIGGS schemas and dictionaries so that it can validate files offline.
That material keeps its own licence:

| Material | Source | Licence |
| --- | --- | --- |
| DIGGS XML schemas, deprecation registry | [DIGGSml/schemas](https://github.com/DIGGSml/schemas), [DIGGSml/schema-dev](https://github.com/DIGGSml/schema-dev) | MPL-2.0 |
| DIGGS dictionaries (code lists, properties, units) | [DIGGSml/def](https://github.com/DIGGSml/def) | MIT, as its README states |
| Energistics unit definitions in the DIGGS units dictionary | Energistics, via DIGGSml/def | [Energistics Product License Agreement](https://energistics.org/product-license-agreement) |
| DIGGS Schematron rules (adapted) | [DIGGSml/validation](https://github.com/DIGGSml/validation) | Not stated upstream; pyDIGGS's changes are Apache-2.0 |
| Energistics and WITSML schemas used by the DIGGS profile | Energistics, via the DIGGS schemas | [Energistics Product License Agreement](https://energistics.org/product-license-agreement) |
| OGC XLink schema | OGC, via the DIGGS schemas | OGC copyright notice |
| EPSG coordinate reference system facts | [EPSG Dataset](https://epsg.org) (IOGP) | [EPSG Dataset Terms of Use](https://epsg.org/terms-of-use.html) |

The licence of every file in the repository is recorded in
[`REUSE.toml`](https://github.com/xinp-hub/pydiggs/blob/master/REUSE.toml) in the
[REUSE](https://reuse.software) format, with the licence texts in
[`LICENSES/`](https://github.com/xinp-hub/pydiggs/tree/master/LICENSES); CI checks that
every file is covered. The exact upstream commit of each bundled file is in
[`PROVENANCE.toml`](https://github.com/xinp-hub/pydiggs/blob/master/src/pydiggs/PROVENANCE.toml).

pyDIGGS is an independent project. It is not produced or endorsed by the DIGGS project
or its sponsors, Energistics, OGC, IOGP or the AGS.

## Professional use

pyDIGGS checks that a DIGGS file is well formed, follows the schemas, uses the
dictionaries correctly and passes the published DIGGS rules. It does **not** check that
the data is correct as engineering: that the soil descriptions, test results, units or
locations describe what is actually in the ground.

- A file that passes pyDIGGS is a file other software can read reliably. It is not
  reviewed data.
- Data used for design, construction or regulatory submissions still needs review by a
  qualified professional under your normal quality procedures.
- Record the pyDIGGS version you used (`pydiggs --version`) with your results, so the
  check can be repeated.

The software is provided "as is", without warranty of any kind, and the authors are not
liable for its use; see sections 7 and 8 of the licence.

## Contributing

Contributions are licensed under Apache-2.0, the same as the rest of pyDIGGS, under
section 5 of the licence. There is no contributor licence agreement and no copyright
assignment: contributors keep the copyright in their work. Instead, each commit is signed
off (`git commit -s`), certifying the
[Developer Certificate of Origin 1.1](https://developercertificate.org/), and CI checks
the sign-off. See the [Contributing guide](contributing.md).
