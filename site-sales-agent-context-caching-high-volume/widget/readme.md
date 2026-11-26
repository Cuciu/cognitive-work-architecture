# Widget

Two deployment options:

## Option A: Single-file (widget.html)
Paste before `</body>`. HTML + CSS + JS inline.
Zero dependencies. Best for WordPress, Webflow, static sites.

## Option B: Separate files (widget.html + widget.js)
Split deployment. JS loaded via `<script src="widget.js">`.
Better for SPAs or when CSP restricts inline scripts.

Both are functionally identical.
