# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from data_collection.page_access import (
    KPF_COMPOUND_HEAD,
    KPF_COMPOUND_TAIL,
    PAGE_PRESENT_BIT,
    PageAccessRequest,
    PageAccessTracker,
    ResolvedPhysicalPage,
    decode_pagemap_entry,
    idle_bitmap_offset_and_mask,
)


class _FakeSamplingTracker(PageAccessTracker):
    def __init__(self):
        super().__init__(page_size=4096, proc_root=ROOT)
        self.resolved = dict[tuple[int, int], ResolvedPhysicalPage]()
        self.idle_bits = dict[int, bool]()
        self.mark_calls = list[tuple[int, ...]]()

    def _resolve_request(self, request: PageAccessRequest) -> ResolvedPhysicalPage:
        return self.resolved[(request.pid, request.virtual_address)]

    def _read_page_idle(self, pfn: int) -> bool:
        return self.idle_bits[pfn]

    def _mark_idle_pfns(self, pfns):
        self.mark_calls.append(tuple(sorted(pfns)))


class _FakeFlagTracker(PageAccessTracker):
    def __init__(self, flags_by_pfn: dict[int, int]):
        super().__init__(page_size=4096, proc_root=ROOT)
        self.flags_by_pfn = flags_by_pfn

    def _read_kpageflags(self, pfn: int) -> int:
        return self.flags_by_pfn.get(pfn, 0)


class PageAccessHelpersTest(unittest.TestCase):
    def test_decode_pagemap_entry_requires_present_bit(self):
        self.assertIsNone(decode_pagemap_entry(123))
        self.assertEqual(decode_pagemap_entry(PAGE_PRESENT_BIT | 77), 77)

    def test_idle_bitmap_offset_and_mask(self):
        self.assertEqual(idle_bitmap_offset_and_mask(0), (0, 1))
        self.assertEqual(idle_bitmap_offset_and_mask(63), (0, 1 << 63))
        self.assertEqual(idle_bitmap_offset_and_mask(64), (8, 1))

    def test_normalize_tracking_pfn_walks_back_to_compound_head(self):
        tracker = _FakeFlagTracker(
            {
                100: 1 << KPF_COMPOUND_HEAD,
                101: 1 << KPF_COMPOUND_TAIL,
                102: 1 << KPF_COMPOUND_TAIL,
            }
        )
        self.assertEqual(tracker._normalize_tracking_pfn(102), 100)
        self.assertEqual(tracker._normalize_tracking_pfn(100), 100)


class PageAccessSamplingTest(unittest.TestCase):
    def test_sample_many_dedupes_pfns_and_marks_first_sample_invalid(self):
        tracker = _FakeSamplingTracker()
        request_a = PageAccessRequest(pid=17, virtual_address=0x1000)
        request_b = PageAccessRequest(pid=17, virtual_address=0x2000)
        shared_page = ResolvedPhysicalPage(
            pid=17,
            virtual_address=0x1000,
            mapped_pfn=33,
            tracking_pfn=33,
            physical_page_addr=33 * 4096,
            tracking_physical_page_addr=33 * 4096,
        )
        tracker.resolved[(17, 0x1000)] = shared_page
        tracker.resolved[(17, 0x2000)] = ResolvedPhysicalPage(
            pid=17,
            virtual_address=0x2000,
            mapped_pfn=33,
            tracking_pfn=33,
            physical_page_addr=33 * 4096,
            tracking_physical_page_addr=33 * 4096,
        )

        first = tracker.sample_many([request_a, request_b])
        self.assertEqual(tracker.mark_calls, [(33,)])
        self.assertFalse(first[0].access_bit_valid)
        self.assertFalse(first[1].access_bit_valid)
        self.assertIsNone(first[0].access_bit)
        self.assertIsNone(first[1].access_bit)

        tracker.idle_bits[33] = False
        second = tracker.sample_many([request_a, request_b])
        self.assertEqual(tracker.mark_calls, [(33,), (33,)])
        self.assertTrue(second[0].access_bit_valid)
        self.assertTrue(second[1].access_bit_valid)
        self.assertTrue(second[0].access_bit)
        self.assertTrue(second[1].access_bit)

    def test_unmapped_pages_return_invalid_result(self):
        tracker = _FakeSamplingTracker()
        tracker.resolved[(5, 0x3000)] = ResolvedPhysicalPage(
            pid=5,
            virtual_address=0x3000,
            mapped_pfn=None,
            tracking_pfn=None,
            physical_page_addr=None,
            tracking_physical_page_addr=None,
        )

        result = tracker.sample(5, 0x3000)
        self.assertFalse(result.access_bit_valid)
        self.assertIsNone(result.access_bit)
        self.assertIsNone(result.mapped_pfn)


if __name__ == "__main__":
    unittest.main()
