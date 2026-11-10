"""Shared presentation for the local demo pages."""

from base64 import b64encode
from hashlib import sha256

from jinja2 import DictLoader, Environment


STYLE = """
body{font:1rem system-ui,sans-serif;color:#182435;margin:0;background:#f3f6fa}
main{max-width:70rem;margin:0 auto;padding:2.5rem 1.5rem}
h1{margin:.1rem 0 .5rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr));gap:1rem;margin-top:1.5rem}
.tile{display:flex;flex-direction:column;gap:.65rem;background:white;border:1px solid #cdd7e5;border-radius:.75rem;padding:1.25rem}
a.tile{color:inherit;text-decoration:none}
a.tile:hover,a.tile:focus-visible{border-color:#214b83;text-decoration:underline}
.tile h2,.tile p{margin:0}.tile p{min-height:3em;color:#3a4a62}
.tile label{display:flex;flex-direction:column;gap:.25rem}
.tile input,.tile button{font:inherit;padding:.5rem .7rem;border-radius:.4rem}
.tile input{border:1px solid #8594a8;min-width:0}
.tile button{border:1px solid #214b83;background:#214b83;color:white;margin-top:.4rem;cursor:pointer}
.login{max-width:29rem}
.login form{margin-top:1.25rem;gap:1rem}
.login label{font-weight:600}
.login input,.login button,.login .back{min-height:2.75rem;box-sizing:border-box}
.login button,.login .back{display:block;width:100%;text-align:center;font-weight:600}
.login .back{border:1px solid #8594a8;border-radius:.4rem;padding:.6rem;color:#214b83;text-decoration:none}
.login .back:hover{background:#f3f6fa}
.login :focus-visible{outline:2px solid #214b83;outline-offset:3px}
.login [role=alert]{color:#95261c}
pre{white-space:pre-wrap;overflow-wrap:anywhere}
.workspace{max-width:62rem}
.toolbar,.menu,.actions{display:flex;align-items:center;flex-wrap:wrap;gap:.65rem}
.toolbar{justify-content:space-between;margin:0 0 1.5rem;padding-bottom:1rem;border-bottom:1px solid #cdd7e5}
.menu a{color:#214b83;padding:.55rem .7rem;border-radius:.4rem;text-decoration:none}
.menu a:hover,.menu a[aria-current=page]{background:#e1eaf6;text-decoration:underline}
.workspace :focus-visible{outline:2px solid #214b83;outline-offset:3px}
.panel,.workspace details{background:white;border:1px solid #cdd7e5;border-radius:.75rem;padding:1rem 1.25rem;margin:1rem 0;min-width:0}
.panel h2:first-child,.panel h3:first-child{margin-top:0}
.case-row{border-top:1px solid #cdd7e5;padding:.75rem 0;overflow-wrap:anywhere}
.workspace summary{cursor:pointer;font-weight:600;color:#214b83}
.workspace fieldset{min-width:0;margin:1rem 0;border:1px solid #cdd7e5;border-radius:.5rem}
.workspace legend{font-weight:600}
.workspace label{font-weight:600}
.fields label{display:block}
.fields label:has(input[type=checkbox]){display:flex;align-items:center;gap:.5rem}
.workspace input:not([type=checkbox],[type=hidden]),.workspace select,.workspace textarea{
 font:inherit;box-sizing:border-box;max-width:100%;padding:.55rem .65rem;border:1px solid #8594a8;border-radius:.4rem;color:inherit;background:white
}
.fields input:not([type=checkbox],[type=hidden]),.fields select,.workspace textarea{display:block;width:100%;margin:.35rem 0 .75rem}
.workspace textarea{resize:vertical}
.workspace button{font:inherit;font-weight:600;padding:.55rem .8rem;border:1px solid #214b83;border-radius:.4rem;background:#214b83;color:white;cursor:pointer}
.workspace button:hover{background:#183962}
.workspace .secondary{color:#214b83;background:white}
.workspace .secondary:hover{background:#e1eaf6}
.workspace .danger{color:#95261c;background:white;border-color:#95261c}
.workspace .danger:hover{background:#faeae8}
.workspace [role=status],.workspace [role=alert]{padding:.75rem 1rem;border-radius:.4rem;background:#e1eaf6}
.workspace [role=alert]{color:#95261c;background:#faeae8}
.workspace pre{padding:1rem;background:#edf2f8;border-radius:.4rem}
.workspace a{color:#214b83}
"""
STYLE_HASH = b64encode(sha256(STYLE.encode("utf-8")).digest()).decode("ascii")

_LAYOUT = """
{% macro toolbar(role, active) %}
<header class="toolbar">
<nav class="menu" aria-label="{{ role }} menü">
{% if role == "Üzemeltető" %}
{{ item("/operator/connections", "Modellkapcsolatok", active) }}
{{ item("/operator/profiles", "Hívási profilok", active) }}
{{ item("/operator/recognizer", "Névfelismerő telepítése", active) }}
{% else %}
{{ item("/", "Munkakosár", active) }}
{% if role == "Kérő" %}
{{ item("/own-input", "Saját szöveg", active) }}
{{ item("/sensitive-values", "Mentett érzékeny elemek", active) }}
{{ item("/recognizer-setup", "Névfelismerő állapota", active) }}
{% endif %}
{% endif %}
</nav>
<form method="post" action="{{ '/operator/logout' if role == 'Üzemeltető' else '/logout' }}">
<button class="secondary">Kilépés</button>
</form>
</header>
{% endmacro %}
{% macro item(path, title, active) %}
<a href="{{ path }}"{% if active == path %} aria-current="page"{% endif %}>{{ title }}</a>
{% endmacro %}
"""
TEMPLATES = Environment(autoescape=True, loader=DictLoader({"workspace": _LAYOUT}))
TEMPLATES.globals["page_style"] = STYLE
