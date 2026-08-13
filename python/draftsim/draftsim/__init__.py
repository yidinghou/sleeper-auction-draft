"""A fantasy football auction draft, modelled as a record of sales.

Two ideas hold the package together:

- The record of sales is the only truth. Who owns whom, what a seat has spent,
  what it can still bid and what its best lineup is are all computed from that
  record and can be thrown away.
- A player is worth what he adds to *your starting lineup* over a
  freely-available body — not what he scores.

Import from the modules directly; this package exports no shortcuts, so that
every import says which part of the model it is reaching for.
"""
