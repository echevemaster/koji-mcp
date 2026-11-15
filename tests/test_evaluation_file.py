"""The evaluation set follows the mcp-builder format: 10 question/answer pairs."""

import xml.etree.ElementTree as ET
from pathlib import Path

EVAL_FILE = Path(__file__).resolve().parent.parent / "evals" / "koji_evaluation.xml"


def test_evaluation_file_has_ten_answerable_pairs():
    root = ET.parse(EVAL_FILE).getroot()
    assert root.tag == "evaluation"
    pairs = root.findall("qa_pair")
    assert len(pairs) == 10
    questions = [p.findtext("question", "").strip() for p in pairs]
    answers = [p.findtext("answer", "").strip() for p in pairs]
    assert all(questions) and all(answers)
    assert len(set(questions)) == 10
    # A single verifiable value, not a sentence.
    assert all("\n" not in a and len(a) <= 80 for a in answers)
