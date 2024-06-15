"""Full implementation is from the original RankGPT repository https://github.com/sunnweiwei/RankGPT under its Apache 2.0 License

Changes made are:
- Truncating the file to only the relevant functions
- Using only LiteLLM
- make_item() added
- Packaging it onto RankGPTRanker"""

import copy
from typing import Optional, Union, List, Dict
from litellm import completion
from rerankers.models.ranker import BaseRanker
from rerankers.documents import Document
from rerankers.results import RankedResults, Result
from rerankers.utils import vprint, prep_docs


def get_prefix_prompt(query, num):
    return [
        {
            "role": "system",
            "content": "You are MatchRankGPT, an intelligent assistant designed to rank dating profile bios based on their compatibility with a given prospect's bio. Your goal is to identify the best potential matches for the prospect, considering factors such as shared interests, values, political leanings, religion, the desire for children, and life goals are important. Focus on the overall compatibility and potential for a successful long-term relationship, rather than just surface-level similarities.",
        },
        {
            "role": "user",
            "content": f"I will provide you with a set of {num} candidate passages, each representing a dating profile bio. \nYour task is to rank these passages, which is a similar dating profile bio, based on their compatibility, considering factors such as shared interests, values, and lifestyle, in terms of relevance to the provided query: {query}",
        },
        {"role": "assistant", "content": "Understood. I will rank the candidate profiles based on their compatibility and potential for romantic interest with the given query profile to make the most accurate ranking possible. Please provide the profiles and the query profile."},
    ]


def get_post_prompt(query, num):
    return f"Search Query: {query}. \nRank the {num} passages above based on their relevance to the search query and their potential to yield a successful romantic match. The passages should be listed in descending order using identifiers. The passages with the highest potential for a successful romantic match should be listed first. The output format should be [] > [], e.g., [1] > [2]. Only response the ranking results, do not say any word or explain."


def create_permutation_instruction(
    item=None,
    rank_start=0,
    rank_end=100,
    lang: str = "en",
):
    query = item["query"]
    num = len(item["hits"][rank_start:rank_end])

    max_length = 300

    messages = get_prefix_prompt(query, num)
    rank = 0
    for hit in item["hits"][rank_start:rank_end]:
        rank += 1
        content = hit["content"]
        content = content.replace("Title: Content: ", "")
        content = content.strip()
        # For Japanese should cut by character: content = content[:int(max_length)]
        if lang in ["zh", "ja"]:
            content = content[: int(max_length)]
        else:
            content = " ".join(content.split()[: int(max_length)])
        messages.append({"role": "user", "content": f"[{rank}] {content}"})
        messages.append({"role": "assistant", "content": f"Received passage [{rank}]."})
    messages.append({"role": "user", "content": get_post_prompt(query, num)})

    return messages


def clean_response(response: str):
    new_response = ""
    for c in response:
        if not c.isdigit():
            new_response += " "
        else:
            new_response += c
    new_response = new_response.strip()
    return new_response


def remove_duplicate(response):
    new_response = []
    for c in response:
        if c not in new_response:
            new_response.append(c)
    return new_response


def receive_permutation(item, permutation, rank_start=0, rank_end=100):
    response = clean_response(permutation)
    response = [int(x) - 1 for x in response.split()]
    response = remove_duplicate(response)
    cut_range = copy.deepcopy(item["hits"][rank_start:rank_end])
    original_rank = [tt for tt in range(len(cut_range))]
    response = [ss for ss in response if ss in original_rank]
    response = response + [tt for tt in original_rank if tt not in response]
    for j, x in enumerate(response):
        item["hits"][j + rank_start] = copy.deepcopy(cut_range[x])
        if "rank" in item["hits"][j + rank_start]:
            item["hits"][j + rank_start]["rank"] = cut_range[j]["rank"]
        if "score" in item["hits"][j + rank_start]:
            item["hits"][j + rank_start]["score"] = cut_range[j]["score"]
    return item


def make_item(
    query: str, docs: List[str]
) -> Dict[str, Union[List[Dict[str, str]], str]]:
    return {
        "query": query,
        "hits": [{"content": doc} for doc in docs],
    }


class RankGPTRanker(BaseRanker):
    def __init__(
        self, model: str, api_key: str, lang: str = "en", verbose: int = 1
    ) -> "RankGPTRanker":
        self.api_key = api_key
        self.model = model
        self.verbose = verbose
        self.lang = lang

    def _query_llm(self, messages: List[Dict[str, str]]) -> str:
        response = completion(
            api_key=self.api_key, model=self.model, messages=messages, temperature=0
        )
        return response.choices[0].message.content

    def rank(
        self,
        query: str,
        docs: Union[str, List[str], Document, List[Document]],
        doc_ids: Optional[Union[List[str], List[int]]] = None,
        metadata: Optional[List[dict]] = None,
        rank_start: int = 0,
        rank_end: int = 0,
    ) -> RankedResults:
        docs = prep_docs(docs, doc_ids, metadata)

        item = make_item(query, [d.text for d in docs])
        messages = create_permutation_instruction(
            item=item,
            rank_start=rank_start,
            rank_end=rank_end,
            lang=self.lang,
        )
        vprint(f"Querying model {self.model} with via LiteLLM...", self.verbose)
        permutation = self._query_llm(messages)
        item = receive_permutation(
            item, permutation, rank_start=rank_start, rank_end=rank_end
        )
        ranked_docs = []
        for idx, doc in enumerate(item["hits"]):
            ranked_docs.append(
                Result(
                    document=list(filter(lambda x: x.text == doc["content"], docs))[0],
                    rank=idx + 1,
                )
            )
        ranked_results = RankedResults(
            results=ranked_docs, query=query, has_scores=False
        )
        return ranked_results

    def score(self):
        print("Listwise ranking models like RankGPT-4 cannot output scores!")
        return None
