# Closed registers through the professional's own access («bring your own access»)

Owner's requirement (2026-10-26): when a notary (or a lawyer, private executor, arbitration manager) buys or rents the
system, the registers closed to the public must be reachable **through that person's own legal access**, and the result
must land in their project in the graph. This page is the design and the legal frame; nothing of it is built yet.

## What a notary can open that the public cannot

| Register | Public? | Notary's access | Holds |
|---|---|---|---|
| ЄДР, full extract | partly (open dump: no address, no РНОКПП, no КВЕД) | full, with РНОКПП of founders / ФОП | people behind companies |
| Державний реєстр актів цивільного стану (ДРАЦС) | no | yes (inheritance, family status), each query grounded | birth, marriage, divorce, death, name change |
| Спадковий реєстр | no | yes | wills, inheritance cases |
| Єдиний реєстр довіреностей | no | yes | powers of attorney |
| Державний реєстр речових прав на нерухоме майно (ДРРП) | yes, paid, with КЕП, by name or code | yes, full | property of a person / company |
| Реєстр обтяжень рухомого майна | yes, paid | yes | pledged cars, equipment |
| Єдиний реєстр боржників | yes, open data | — | already in the lake |

All of them are run by НАІС (ДП «Національні інформаційні системи», Minjust). Access is personal: a КЕП of the
professional, a web cabinet or the notary's workstation software, every query logged under their key. There is no
public API for third parties; НАІС integrates with vendors of notary software under contract.

## Design

1. **The system never holds a professional's credentials.** No КЕП file, no password, no session token stored on our
   side. The connection lives on the professional's own computer.
2. **Connector = a local module on their workstation** (a small desktop app or browser extension) that signs in with
   their КЕП to the НАІС cabinets, runs the queries the professional triggers from their project, and sends the results
   to *their private project only* (the project's encrypted store, not the shared graph). Each result carries: register,
   time, the professional's identifier, the ground for the query (the notarial action / case number they typed).
3. **Audit log in the project.** Every closed-register query is listed and exportable: the professional must be able
   to show the Ministry what was asked and why.
4. **Scope by role.** A notary's project may call ДРАЦС, Спадковий, Довіреності, ЄДР full, ДРРП; a lawyer's project
   ДРРП and ЄДР full; a journalist's project only public sources. Roles come from the account system of the roadmap.
5. **Family and indirect links.** A ДРАЦС record (marriage, birth) becomes a `family` edge in the private project with
   the register as source; the graph then walks 4–5 hops (web/api/graph.js allows depth up to 5 since 2026-10-26)
   across public and private edges, and «find links» (projects, roadmap weeks 3–4) runs over both.
6. **Legal frame.** ЗУ «Про нотаріат», the Minjust orders on access to ДРАЦС / Спадковий реєстр for notaries, ЗУ «Про
   захист персональних даних». Each query must have a ground (a notarial action, an inheritance case). A query without a
   ground is the professional's liability; the system records the ground they entered and refuses an empty one.

## Order of work

1. **ДРРП first.** Public, paid, works with any КЕП: «Отримання інформації з ДРРП» by name / code. The connector's first
   register; also useful to lawyers and journalists with their own КЕП.
2. **ЄДР full extracts** through the same cabinet (КЕП of anyone): РНОКПП of founders / ФОП, addresses, КВЕД.
3. **ДРАЦС, Спадковий реєстр, Довіреності** for notaries: needs a notary partner for testing and, likely, a
   conversation with НАІС about integration terms.

## What this does not change

The public graph stays public and stays on open data. A closed register's result never leaves the project of the
professional who asked for it.
