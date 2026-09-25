"""录音覆盖缺口审计：识别字符覆盖互补、两侧 matched 定位与 computed 相交段。

处理流程：
1. 把每个正时长识别字符的半开毫秒区间 [start, end) 视为覆盖
   （零时长字符不覆盖），在录音 [0, total_duration) 内求互补，
   得到按时间升序的缺口序列（录音首尾的空洞同样保留）；
2. 仅保留时长不少于 min_gap_ms 的缺口；
3. 对每个缺口，按 matched_index 找到缺口两侧最近的 matched 修订字符
   （缺侧为 None），并收集时间与缺口相交的 computed 修订字符，
   把相邻下标合并为连续半开下标段，便于听审区分未识别音频与插值文字。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

from .schemas import (
    ComputedGapSpan,
    CoverageGap,
    RecognizedChar,
    RevisedCharTimeline,
)


def _raw_gaps(recognized: list[RecognizedChar],
              total_duration: int) -> list[tuple[int, int]]:
    """正时长识别字符区间在 [0, total_duration) 内的互补（按时间升序）。

    前置条件：识别字符已校验为按起始时间排序且互不重叠，故游标单调前进；
    零时长字符（start == end）不产生覆盖，直接跳过。
    """
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for item in recognized:
        if item.end <= item.start:  # 零时长字符不覆盖
            continue
        if item.start > cursor:
            gaps.append((cursor, item.start))
        cursor = item.end
    if cursor < total_duration:
        gaps.append((cursor, total_duration))
    return gaps


def build_coverage_gaps(
        recognized: list[RecognizedChar],
        chars: list[RevisedCharTimeline],
        min_gap_ms: int,
        total_duration: int) -> list[CoverageGap]:
    """生成覆盖缺口审计结果，按缺口起始时间升序返回。"""
    gaps = [(s, e) for s, e in _raw_gaps(recognized, total_duration)
            if e - s >= min_gap_ms]
    if not gaps:
        return []

    # 已校验有序不重叠：start 与 end 均非递减，可二分。
    starts = [it.start for it in recognized]
    ends = [it.end for it in recognized]
    # matched 修订字符的 (matched_index, 修订下标)；对齐保序故 matched_index 严格递增。
    matched = [(c.matched_index, j) for j, c in enumerate(chars)
               if c.source == "matched"]
    matched_ids = [mi for mi, _ in matched]
    # computed 修订字符的 (修订下标, start, end)；时间轴不回退故时间非递减。
    computed = [(j, c.start, c.end) for j, c in enumerate(chars)
                if c.source == "computed"]
    comp_starts = [s for _, s, _ in computed]
    comp_ends = [e for _, _, e in computed]

    result: list[CoverageGap] = []
    for gap_start, gap_end in gaps:
        # 缺口左侧最后一个识别字符下标（end <= gap_start）与右侧第一个
        # 识别字符下标（start >= gap_end），再按 matched_index 定位两侧
        # 最近的 matched 修订字符；一侧没有识别字符或没有任何 matched 字符
        # 落在该侧时为 None。
        left = bisect_right(ends, gap_start) - 1
        prev_index = None
        if left >= 0:
            k = bisect_right(matched_ids, left) - 1
            if k >= 0:
                prev_index = matched[k][1]

        right = bisect_left(starts, gap_end)
        next_index = None
        if right < len(recognized):
            k = bisect_left(matched_ids, right)
            if k < len(matched):
                next_index = matched[k][1]

        # 与缺口 [gap_start, gap_end) 相交的 computed 字符：
        # start < gap_end 且 end > gap_start。时间非递减，故相交者构成
        # computed 序列的连续子区间，再按修订下标相邻关系分段。
        lo = bisect_right(comp_ends, gap_start)
        hi = bisect_left(comp_starts, gap_end)
        spans: list[ComputedGapSpan] = []
        for j in range(lo, hi):
            idx = computed[j][0]
            if spans and idx == spans[-1].end_index:
                spans[-1].end_index = idx + 1
            else:
                spans.append(ComputedGapSpan(start_index=idx, end_index=idx + 1))

        result.append(CoverageGap(
            start=gap_start,
            end=gap_end,
            prev_matched_index=prev_index,
            next_matched_index=next_index,
            computed_spans=spans,
        ))
    return result
