# Károly Tamás szakdolgozati prototípusa (2026).
"""Read two local texts from stdin and return only person offsets."""

import json
import sys


def main() -> None:
    import spacy

    texts = json.load(sys.stdin)
    nlp = spacy.load("hu_core_news_md")
    print(json.dumps([
        {"section": section, "kind": "PERSON", "start": entity.start_char, "end": entity.end_char}
        for section, doc in zip(("source", "request"), nlp.pipe(texts), strict=True)
        for entity in doc.ents
        if entity.label_ == "PER"
    ]))


if __name__ == "__main__":
    main()
