# coding: utf8
import json
import sys
from typing import Iterable, Iterator, Optional, Tuple

import spacy
from spacy.tokens import Doc, Span
from spacy.language import Language
from spacy.lang.ja import Japanese, JapaneseTokenizer

from . import set_split_mode, inflection, reading_form, ent_label_ene, ent_label_ontonotes, bunsetu_bi_label, bunsetu_position_type
from .bunsetu_recognizer import bunsetu_available, bunsetu_head_list, bunsetu_phrase_span


class Analyzer:
    def __init__(
        self,
        model_path: str,
        ensure_model: str,
        split_mode: str,
        hash_comment: str,
        output_format: str,
        require_gpu: bool,
        disable_sentencizer: bool,
    ) -> None:
        self.model_path = model_path
        self.ensure_model = ensure_model
        self.split_mode = split_mode
        self.hash_comment = hash_comment
        self.output_format = output_format
        self.require_gpu = require_gpu
        self.disable_sentencizer = disable_sentencizer
        self.nlp: Optional[Language] = None

    def set_nlp(self) -> None:
        if self.nlp:
            return

        if self.require_gpu:
            spacy.require_gpu()

        if self.output_format in ["2", "mecab"]:
            nlp = JapaneseTokenizer(nlp=Japanese(), split_mode=self.split_mode).tokenizer
        else:
            # Work-around for pickle error. Need to share model data.
            if self.model_path:
                nlp = spacy.load(self.model_path)
            elif self.ensure_model:
                nlp = spacy.load(self.ensure_model.replace("-", "_"))
            else:
                try:
                    nlp = spacy.load("ja_ginza_electra")
                except IOError as e:
                    try:
                        nlp = spacy.load("ja_ginza")
                    except IOError as e:
                        print(
                            'Could not find the model. You need to install "ja-ginza-electra" or "ja-ginza" by executing pip like `pip install ja-ginza-electra`.',
                            file=sys.stderr,
                        )
                        raise e

            if self.disable_sentencizer:
                nlp.add_pipe("disable_sentencizer", before="parser")

            if self.split_mode:
                set_split_mode(nlp, self.split_mode)

        self.nlp = nlp

    def analyze_batch(self, lines: Iterable[str]) -> str:
        self.set_nlp()
        if self.output_format in ["2", "mecab"]:
            return "".join(self.analyze_line(line) for line in lines)

        if self.hash_comment == "print":
            batch = self.nlp.pipe(line for line in lines if not line.startswith("#"))
            docs = []
            index = 0
            for line in lines:
                if line.startswith("#"):
                    docs.append(line)
                else:
                    docs.append(batch[index])
                    index += 1
        else:
            if self.hash_comment == "skip":
                lines = [line for line in lines if not line.startswith("#")]
            docs = self.nlp.pipe(lines)
 
        if self.output_format in ["3", "json"]:
            sep = ",\n"
        else:
            sep = ""
        return sep.join(format_doc(doc, self.output_format) if isinstance(doc, Doc) else doc for doc in docs)

    def analyze_line(self, input_line: str) -> str:
        line = input_line.rstrip("\n")
        if line.startswith("#"):
            if self.hash_comment == "print":
                return input_line
            elif self.hash_comment == "skip":
                return ""
        if line == "":
            return "\n"
        if self.output_format in ["2", "mecab"]:
            doc = self.nlp.tokenize(line)
        else:
            doc = self.nlp(line)
        return format_doc(doc, self.output_format)


def format_doc(
   doc: Doc, output_format: str
) -> str:
    if output_format in ["0", "conllu"]:
        return "".join(format_conllu(sent) for sent in doc.sents)
    elif output_format in ["1", "cabocha"]:
        return "".join(format_cabocha(sent) for sent in doc.sents)
    elif output_format in ["2", "mecab"]:
        return "".join(format_mecab(doc))
    elif output_format in ["3", "json"]:
        return ",\n".join(format_json(sent) for sent in doc.sents)
    else:
        raise Exception(output_format + " is not supported")


def format_json(sent: Span) -> str:
    token_lines = ",\n".join(
        f"""       {{"id":{
            token.i - sent.start + 1
        },"orth":"{
            token.orth_
        }","tag":"{
            token.tag_
        }","pos":"{
            token.pos_
        }","lemma":"{
            token.lemma_
        }","head":{
            token.head.i - token.i
        },"dep":"{
            token.dep_
        }","ner":"{
            token.ent_iob_
        }{
            "-" + token.ent_type_ if token.ent_type_ else ""
        }"{
            ',"whitespacce":"' + token.whitespace_ + '"' if token.whitespace_ else ""
        }}}""" for token in sent
    )
    return f""" {{
  "paragraphs": [
   {{
    "raw": "{sent.text}",
    "sentences": [
     {{
      "tokens": [
{token_lines}
      ]
     }}
    ]
   }}
  ]
 }}"""


def format_conllu(sent: Span, print_origin=True) -> str:
    np_labels = [""] * len(sent)
    use_bunsetu = bunsetu_available(sent)
    if use_bunsetu:
        for head_i in bunsetu_head_list(sent):
            bunsetu_head_token = sent[head_i]
            phrase = bunsetu_phrase_span(bunsetu_head_token)
            if phrase.label_ == "NP":
                for idx in range(phrase.start - sent.start, phrase.end - sent.start):
                    np_labels[idx] = "NP_B" if idx == phrase.start else "NP_I"
    token_lines = "".join(conllu_token_line(sent, token, np_label, use_bunsetu) for token, np_label in zip(sent, np_labels))
    if print_origin:
        return f"# text = {sent.text}\n{token_lines}\n"
    else:
        return f"{token_lines}\n"


def conllu_token_line(sent, token, np_label, use_bunsetu) -> str:
    bunsetu_bi = bunsetu_bi_label(token) if use_bunsetu else None
    position_type = bunsetu_position_type(token) if use_bunsetu else None
    inf = inflection(token)
    reading = reading_form(token)
    ne = ent_label_ontonotes(token)
    ene = ent_label_ene(token)
    misc = "|".join(
        filter(
            lambda s: s,
            (
                "SpaceAfter=Yes" if token.whitespace_ else "SpaceAfter=No",
                "" if not bunsetu_bi else "BunsetuBILabel={}".format(bunsetu_bi),
                "" if not position_type else "BunsetuPositionType={}".format(position_type),
                np_label,
                "" if not inf else "Inf={}".format(inf),
                "" if not reading else "Reading={}".format(reading.replace("|", "\\|").replace("\\", "\\\\")),
                "" if not ne or ne == "O" else "NE={}".format(ne),
                "" if not ene or ene == "O" else "ENE={}".format(ene),
            )
        )
    )

    return "\t".join(
        [
            str(token.i - sent.start + 1),
            token.orth_,
            token.lemma_,
            token.pos_,
            token.tag_.replace(",*", "").replace(",", "-"),
            "NumType=Card" if token.pos_ == "NUM" else "_",
            "0" if token.head.i == token.i else str(token.head.i - sent.start + 1),
            token.dep_.lower() if token.dep_ else "_",
            "_",
            misc if misc else "_",
        ]
    ) + "\n"


def format_cabocha(sent: Span) -> str:
    bunsetu_index_list = {}
    bunsetu_index = -1
    for token in sent:
        if bunsetu_bi_label(token) == "B":
            bunsetu_index += 1
        bunsetu_index_list[token.i] = bunsetu_index

    lines = []
    for token in sent:
        if bunsetu_bi_label(token) == "B":
            lines.append(cabocha_bunsetu_line(sent, bunsetu_index_list, token))
        lines.append(cabocha_token_line(token))
    lines.append("EOS\n\n")
    return "".join(lines)


def cabocha_bunsetu_line(sent: Span, bunsetu_index_list, token) -> str:
    bunsetu_head_index = None
    bunsetu_dep_index = None
    bunsetu_func_index = None
    dep_type = "D"
    for t in token.doc[token.i : sent.end]:
        if bunsetu_index_list[t.i] != bunsetu_index_list[token.i]:
            if bunsetu_func_index is None:
                bunsetu_func_index = t.i - token.i
            break
        tbi = bunsetu_index_list[t.head.i]
        if bunsetu_index_list[t.i] != tbi:
            bunsetu_head_index = t.i - token.i
            bunsetu_dep_index = tbi
        if bunsetu_func_index is None and bunsetu_position_type(t) in {"FUNC", "SYN_HEAD"}:
            bunsetu_func_index = t.i - token.i
    else:
        if bunsetu_func_index is None:
            bunsetu_func_index = len(sent) - token.i
    if bunsetu_head_index is None:
        bunsetu_head_index = 0
    if bunsetu_dep_index is None:
        bunsetu_dep_index = -1
    return "* {} {}{} {}/{} 0.000000\n".format(
        bunsetu_index_list[token.i],
        bunsetu_dep_index,
        dep_type,
        bunsetu_head_index,
        bunsetu_func_index,
    )


def cabocha_token_line(token) -> str:
    part_of_speech = token.tag_.replace("-", ",")
    part_of_speech += ",*" * (3 - part_of_speech.count(",")) + "," + inflection(token)
    reading = reading_form(token)
    return "{}\t{},{},{},{}\t{}\n".format(
        token.orth_,
        part_of_speech,
        token.lemma_,
        reading if reading else token.orth_,
        "*",
        "O" if token.ent_iob_ == "O" else "{}-{}".format(token.ent_iob_, token.ent_type_),
    )


def format_mecab(sudachipy_tokens) -> str:
    return "".join(mecab_token_line(t) for t in sudachipy_tokens) + "EOS\n\n"


def mecab_token_line(token) -> str:
    reading = token.reading_form()
    return "{}\t{},{},{},{}\n".format(
        token.surface(),
        ",".join(token.part_of_speech()),
        token.normalized_form(),
        reading if reading else token.surface(),
        "*",
    )
