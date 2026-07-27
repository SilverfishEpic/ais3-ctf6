"""The local human web client for Memory Core.

A single-user, loopback-only server that serves one static page and forwards ``/api/*`` to
Memory Core through the same HTTP contract ``ctfmem`` uses. It is a client in exactly the
sense the protocol means: it stores nothing, allocates no identifiers, computes no diffs,
and calls no Summarizer.

Rationale for the shape, including why a same-origin proxy rather than a static page plus
a permissive origin policy on the authoritative service, is recorded in
``docs/adr/0001-human-web-ui.md``.
"""

from __future__ import annotations
