# scripts/

Standalone scripts that complement the `pjb-pipeline` CLI.

| Script              | What it does                                                                                              |
| ------------------- | --------------------------------------------------------------------------------------------------------- |
| `merge_graphs.py`   | Merge per-volume JSON-LD graphs into one corpus graph. Same as `pjb-pipeline merge-graphs`, but importable from Python so you can plug it into a larger pipeline (e.g. add NER output before merging). |

Usage:

```bash
python scripts/merge_graphs.py corpus.jsonld output/*/graph/*.jsonld
```
