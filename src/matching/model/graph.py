"""근거 그래프 — Object를 담고, Link로 잇고, 점수에서 공고 좌표까지 따라간다.

**그래프는 검산하지 않는다.** 위반을 찾는 일은 `governance.check()`가 한다.
여기서 막으면 「깨진 그래프」를 만들 수 없어 검산이 무엇을 잡는지 시험할 수 없다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .objects import Criterion, Evidence, GraphObject, Link, Relation, Requirement, Score

# `trace()`가 따라가는 순서. 근거 사슬 그대로다.
# `contradicts`는 빠져 있다 — 승인 화면에서 뒤집힌 판정을 남기는 관계지 근거 경로가 아니다.
TRACE_CHAIN: tuple[Relation, ...] = (
    "grounded_in",
    "supports",
    "derived_from",
    "extracted_from",
)


class EvidenceGraph(BaseModel):
    """근거 Object와 Link 한 벌. 공고 1개 + 지원자들의 채점 결과가 여기 다 들어간다."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[Requirement] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    scores: list[Score] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)

    # --- 담기 -------------------------------------------------------------

    def add(self, obj: GraphObject) -> None:
        """Object를 종류에 맞는 목록에 넣는다. id가 이미 있으면 거부한다.

        같은 id가 둘이면 `trace()`·`get()`이 어느 쪽을 집을지가 삽입 순서에 달리고,
        그러면 근거가 조용히 다른 것을 가리킨다.
        """
        if isinstance(obj, Requirement):
            bucket: list = self.requirements
        elif isinstance(obj, Criterion):
            bucket = self.criteria
        elif isinstance(obj, Evidence):
            bucket = self.evidence
        elif isinstance(obj, Score):
            bucket = self.scores
        else:
            raise TypeError(f"그래프에 담을 수 없는 종류다: {type(obj).__name__}")

        if self.get(obj.id) is not None:
            raise ValueError(f"id가 이미 있다: {obj.id}")
        bucket.append(obj)

    def link(self, src: str, rel: str, dst: str) -> None:
        """Link를 잇는다. `rel`이 5종 밖이면 pydantic이 여기서 막는다."""
        self.links.append(Link(src=src, rel=rel, dst=dst))  # type: ignore[arg-type]

    # --- 찾기 -------------------------------------------------------------

    def index(self) -> dict[str, GraphObject]:
        """id → Object 표를 그때그때 만든다.

        캐시하지 않는다 — 목록을 직접 만지는 경로(`graph.scores.append(...)`)가 있어
        캐시는 조용히 낡는다. 한 실행의 그래프는 수백 개 규모라 매번 만들어도 싸다.
        """
        table: dict[str, GraphObject] = {}
        for obj in (*self.requirements, *self.criteria, *self.evidence, *self.scores):
            table.setdefault(obj.id, obj)
        return table

    def get(self, obj_id: str) -> GraphObject | None:
        return self.index().get(obj_id)

    def out(self, src: str, rel: str | None = None) -> list[Link]:
        """`src`에서 나가는 Link. 삽입 순서를 유지한다 (근거 문단의 순서가 된다)."""
        return [
            link for link in self.links if link.src == src and (rel is None or link.rel == rel)
        ]

    # --- 따라가기 ---------------------------------------------------------

    def trace(self, score_id: str) -> list[Link]:
        """Score에서 공고 이미지 좌표까지 이어지는 Link 목록. UI가 이걸 쓴다.

        `grounded_in → supports → derived_from → extracted_from` 순으로 간다.
        각 마디에서 **남은 단계 중 처음으로 이어지는 것 하나만** 잡고 다음으로 넘어간다.
        이 「건너뛰기」가 게이트 판정을 살린다 — 게이트 Score는 `grounded_in` 없이
        `derived_from`으로 Requirement에 바로 붙기 때문이다 (검산 G1의 예외).
        """
        found: list[Link] = []
        seen: set[tuple[str, int]] = set()
        frontier: list[tuple[str, int]] = [(score_id, 0)]

        while frontier:
            node, stage = frontier.pop(0)
            if (node, stage) in seen:
                continue
            seen.add((node, stage))

            for depth in range(stage, len(TRACE_CHAIN)):
                links = self.out(node, TRACE_CHAIN[depth])
                if not links:
                    continue
                for link in links:
                    if link not in found:
                        found.append(link)
                    frontier.append((link.dst, depth + 1))
                break

        return found
