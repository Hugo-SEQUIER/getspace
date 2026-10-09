# Licensing

Space is free software under the GNU Affero General Public License, version 3 (AGPL-3.0). [LICENSE](../LICENSE) holds the full terms and [NOTICE](../NOTICE) the copyright statement: Copyright (c) 2026 Hugo SEQUIER. This page summarizes what the license means in practice. It is not legal advice, and the license text decides where the two differ.

## What the license allows

- Run Space for any purpose, including commercial use, on any machine, for yourself or for others.
- Read, study and change the code.
- Share copies, changed or not.
- Offer Space to other people over a network, including as a paid service, provided you meet the conditions below.

## What it asks in return

- **Using it yourself.** Running Space for your own use creates no obligation, even if you change it.
- **Sharing copies.** When you give someone a copy, for example a fork, a package or a container image, it must stay under AGPL-3.0, keep the copyright and license notices, and come with the complete corresponding source code or a written offer of it (sections 4 to 6). A changed version must state that you changed it and when.
- **Network use.** If you change Space and let other people use your changed version over a network, you must offer all of those users the complete source code of your version, at no charge, from a network server (section 13). A "Source" link in the website that leads to your repository or an archive is a common way to do this. This is what distinguishes the AGPL from the GPL: hosting a modified version for others counts, not only handing out copies.
- **No warranty.** Space comes without any warranty, and the authors are not liable for damages (sections 15 and 16).

## Components under other licenses

The Memora text inference runtime vendored in `backend/src/space/_vendor/memora/` comes from [Microsoft Memora](https://github.com/microsoft/Memora) at the pinned commit `dec3f8f2444eace7004fc084abe1be9f3d88270e` and keeps its MIT license. It is not relicensed under the AGPL. Its license file, `backend/src/space/_vendor/memora/LICENSE`, must stay with any copy that includes the runtime; the [vendor manifest](../backend/src/space/_vendor/memora/README.md) lists the changes made for Space.

Every dependency keeps its own license. [THIRD-PARTY.md](../THIRD-PARTY.md) lists the direct dependencies; among them, `psycopg` and `psycopg-pool` are under the LGPL-3.0.

## Contributions

Contributors keep the copyright in their work. Before a first pull request, a contributor accepts the [Contributor License Agreement](../CLA.md), which grants the licensor, Hugo SEQUIER, a broad copyright license and a patent license for the contribution. That license includes the right to relicense contributions under other terms, from another open-source license to a commercial one, and to transfer those rights to a company that takes over the project. Without the agreement, changing the license of code that includes outside contributions would need the consent of every contributor. [CONTRIBUTING.md](../CONTRIBUTING.md) explains the two ways to accept the agreement.

Send signed copies of the agreement, and questions about licensing, to `sequierh@statemethod.com`.
