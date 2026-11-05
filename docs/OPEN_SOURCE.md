# Licensing

Space is free software under the MIT License. [LICENSE](../LICENSE) holds the full terms and [NOTICE](../NOTICE) the copyright statement: Copyright (c) 2026 Hugo SEQUIER. This page summarizes what the license means in practice. It is not legal advice, and the license text decides where the two differ. Commits and releases published before this change were under the GNU Affero General Public License, version 3, and keep those terms; the MIT License applies from this change onward.

## What the license allows

- Run Space for any purpose, including commercial use, on any machine, for yourself or for others.
- Read, study and change the code.
- Share copies, changed or not.
- Offer Space to other people over a network, including as a paid service, and keep your changes to yourself.
- Sublicense and sell copies, including as part of a closed product.

## What it asks in return

- **Keep the notice.** Copies or substantial portions of the software must include the copyright notice and the permission notice.
- **No warranty.** Space comes without any warranty, and the authors are not liable for damages.

The MIT License does not require you to publish your changes, to share the source of a version you run for other people, or to license your own additions under the same terms.

## Components under other licenses

The Memora text inference runtime vendored in `backend/src/space/_vendor/memora/` comes from [Microsoft Memora](https://github.com/microsoft/Memora) at the pinned commit `dec3f8f2444eace7004fc084abe1be9f3d88270e` and keeps its MIT license. Its license file, `backend/src/space/_vendor/memora/LICENSE`, must stay with any copy that includes the runtime; the [vendor manifest](../backend/src/space/_vendor/memora/README.md) lists the changes made for Space.

Every dependency keeps its own license. [THIRD-PARTY.md](../THIRD-PARTY.md) lists the direct dependencies; among them, `psycopg` and `psycopg-pool` are under the LGPL-3.0.

## Contributions

Contributors keep the copyright in their work. Before a first pull request, a contributor accepts the [Contributor License Agreement](../CLA.md), which grants the licensor, Hugo SEQUIER, a broad copyright license and a patent license for the contribution. That license includes the right to relicense contributions under other terms, from another open-source license to a commercial one, and to transfer those rights to a company that takes over the project. Without the agreement, changing the license of code that includes outside contributions would need the consent of every contributor. [CONTRIBUTING.md](../CONTRIBUTING.md) explains the two ways to accept the agreement.

Send signed copies of the agreement, and questions about licensing, to `sequierh@statemethod.com`.
