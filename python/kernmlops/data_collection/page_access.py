from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

PAGEMAP_ENTRY_BYTES = 8
KPAGEFLAGS_ENTRY_BYTES = 8
PAGE_PRESENT_BIT = 1 << 63
PFN_MASK = (1 << 55) - 1
KPF_COMPOUND_HEAD = 15
KPF_COMPOUND_TAIL = 16


@dataclass(frozen=True)
class PageAccessRequest:
    pid: int
    virtual_address: int


@dataclass(frozen=True)
class ResolvedPhysicalPage:
    pid: int
    virtual_address: int
    mapped_pfn: int | None
    tracking_pfn: int | None
    physical_page_addr: int | None
    tracking_physical_page_addr: int | None


@dataclass(frozen=True)
class PageAccessResult:
    pid: int
    virtual_address: int
    mapped_pfn: int | None
    tracking_pfn: int | None
    physical_page_addr: int | None
    tracking_physical_page_addr: int | None
    page_idle: bool | None
    access_bit: bool | None
    access_bit_valid: bool


def decode_pagemap_entry(entry: int) -> int | None:
    if entry & PAGE_PRESENT_BIT == 0:
        return None
    return entry & PFN_MASK


def idle_bitmap_offset_and_mask(pfn: int) -> tuple[int, int]:
    word_index = pfn // 64
    return word_index * 8, 1 << (pfn % 64)


def flag_is_set(flags: int, bit_index: int) -> bool:
    return bool(flags & (1 << bit_index))


class PageAccessTracker:
    def __init__(
        self,
        *,
        page_size: int | None = None,
        proc_root: Path | str = "/proc",
        kpageflags_path: Path | str = "/proc/kpageflags",
        idle_bitmap_path: Path | str = "/sys/kernel/mm/page_idle/bitmap",
    ):
        self.page_size = page_size or os.sysconf("SC_PAGE_SIZE")
        self.proc_root = Path(proc_root)
        self.kpageflags_path = Path(kpageflags_path)
        self.idle_bitmap_path = Path(idle_bitmap_path)

        self._armed_pfns = set[int]()
        self._pagemap_files = dict[int, BinaryIO]()
        self._kpageflags_file: BinaryIO | None = None
        self._idle_bitmap_file: BinaryIO | None = None

    def close(self) -> None:
        for file_obj in self._pagemap_files.values():
            file_obj.close()
        self._pagemap_files.clear()
        if self._kpageflags_file is not None:
            self._kpageflags_file.close()
            self._kpageflags_file = None
        if self._idle_bitmap_file is not None:
            self._idle_bitmap_file.close()
            self._idle_bitmap_file = None

    def invalidate_pid(self, pid: int) -> None:
        file_obj = self._pagemap_files.pop(pid, None)
        if file_obj is not None:
            file_obj.close()

    def sample(self, pid: int, virtual_address: int) -> PageAccessResult:
        return self.sample_many([PageAccessRequest(pid=pid, virtual_address=virtual_address)])[0]

    def resolve_many(
        self,
        requests: Iterable[PageAccessRequest],
    ) -> list[ResolvedPhysicalPage]:
        return [self._resolve_request(request) for request in requests]

    def read_many(
        self,
        resolved_pages: Iterable[ResolvedPhysicalPage],
    ) -> list[PageAccessResult]:
        resolved_list = list(resolved_pages)
        known_pfns = sorted(
            {
                page.tracking_pfn
                for page in resolved_list
                if page.tracking_pfn is not None and page.tracking_pfn in self._armed_pfns
            }
        )
        idle_by_pfn = {pfn: self._read_page_idle(pfn) for pfn in known_pfns}
        return [self._result_from_resolved(page, idle_by_pfn) for page in resolved_list]

    def arm_many(
        self,
        resolved_pages: Iterable[ResolvedPhysicalPage],
    ) -> None:
        tracking_pfns = sorted(
            {
                page.tracking_pfn
                for page in resolved_pages
                if page.tracking_pfn is not None
            }
        )
        if tracking_pfns:
            self._mark_idle_pfns(tracking_pfns)
            self._armed_pfns.update(tracking_pfns)

    def sample_many(
        self,
        requests: Iterable[PageAccessRequest],
    ) -> list[PageAccessResult]:
        resolved = self.resolve_many(requests)
        results = self.read_many(resolved)
        self.arm_many(resolved)
        return results

    def _result_from_resolved(
        self,
        page: ResolvedPhysicalPage,
        idle_by_pfn: dict[int, bool],
    ) -> PageAccessResult:
        if page.tracking_pfn is None:
            return PageAccessResult(
                pid=page.pid,
                virtual_address=page.virtual_address,
                mapped_pfn=page.mapped_pfn,
                tracking_pfn=page.tracking_pfn,
                physical_page_addr=page.physical_page_addr,
                tracking_physical_page_addr=page.tracking_physical_page_addr,
                page_idle=None,
                access_bit=None,
                access_bit_valid=False,
            )

        page_idle = idle_by_pfn.get(page.tracking_pfn)
        access_bit_valid = page.tracking_pfn in idle_by_pfn
        return PageAccessResult(
            pid=page.pid,
            virtual_address=page.virtual_address,
            mapped_pfn=page.mapped_pfn,
            tracking_pfn=page.tracking_pfn,
            physical_page_addr=page.physical_page_addr,
            tracking_physical_page_addr=page.tracking_physical_page_addr,
            page_idle=page_idle,
            access_bit=None if page_idle is None else not page_idle,
            access_bit_valid=access_bit_valid,
        )

    def _resolve_request(self, request: PageAccessRequest) -> ResolvedPhysicalPage:
        mapped_pfn = self._read_mapped_pfn(request.pid, request.virtual_address)
        if mapped_pfn is None:
            return ResolvedPhysicalPage(
                pid=request.pid,
                virtual_address=request.virtual_address,
                mapped_pfn=None,
                tracking_pfn=None,
                physical_page_addr=None,
                tracking_physical_page_addr=None,
            )

        tracking_pfn = self._normalize_tracking_pfn(mapped_pfn)
        return ResolvedPhysicalPage(
            pid=request.pid,
            virtual_address=request.virtual_address,
            mapped_pfn=mapped_pfn,
            tracking_pfn=tracking_pfn,
            physical_page_addr=mapped_pfn * self.page_size,
            tracking_physical_page_addr=tracking_pfn * self.page_size,
        )

    def _read_mapped_pfn(self, pid: int, virtual_address: int) -> int | None:
        if virtual_address < 0:
            raise ValueError("virtual address must be non-negative")

        pagemap_file = self._get_pagemap_file(pid)
        entry_offset = (virtual_address // self.page_size) * PAGEMAP_ENTRY_BYTES
        pagemap_file.seek(entry_offset)
        raw = pagemap_file.read(PAGEMAP_ENTRY_BYTES)
        if len(raw) != PAGEMAP_ENTRY_BYTES:
            raise OSError(
                f"short read from pagemap for pid={pid} va=0x{virtual_address:x}"
            )
        return decode_pagemap_entry(int.from_bytes(raw, "little"))

    def _get_pagemap_file(self, pid: int) -> BinaryIO:
        if not (self.proc_root / str(pid)).is_dir():
            self.invalidate_pid(pid)
            raise ProcessLookupError(f"pid {pid} is not available")

        cached = self._pagemap_files.get(pid)
        if cached is not None and not cached.closed:
            return cached

        pagemap_path = self.proc_root / str(pid) / "pagemap"
        pagemap_file = open(pagemap_path, "rb", buffering=0)
        self._pagemap_files[pid] = pagemap_file
        return pagemap_file

    def _get_kpageflags_file(self) -> BinaryIO:
        if self._kpageflags_file is None or self._kpageflags_file.closed:
            self._kpageflags_file = open(self.kpageflags_path, "rb", buffering=0)
        return self._kpageflags_file

    def _get_idle_bitmap_file(self) -> BinaryIO:
        if self._idle_bitmap_file is None or self._idle_bitmap_file.closed:
            self._idle_bitmap_file = open(self.idle_bitmap_path, "r+b", buffering=0)
        return self._idle_bitmap_file

    def _read_kpageflags(self, pfn: int) -> int:
        kpageflags_file = self._get_kpageflags_file()
        kpageflags_file.seek(pfn * KPAGEFLAGS_ENTRY_BYTES)
        raw = kpageflags_file.read(KPAGEFLAGS_ENTRY_BYTES)
        if len(raw) != KPAGEFLAGS_ENTRY_BYTES:
            raise OSError(f"short read from kpageflags for pfn={pfn}")
        return int.from_bytes(raw, "little")

    def _normalize_tracking_pfn(self, mapped_pfn: int) -> int:
        flags = self._read_kpageflags(mapped_pfn)
        if not flag_is_set(flags, KPF_COMPOUND_TAIL):
            return mapped_pfn

        for candidate in range(mapped_pfn - 1, -1, -1):
            candidate_flags = self._read_kpageflags(candidate)
            if flag_is_set(candidate_flags, KPF_COMPOUND_HEAD):
                return candidate
            if not flag_is_set(candidate_flags, KPF_COMPOUND_TAIL):
                break

        return mapped_pfn

    def _read_page_idle(self, pfn: int) -> bool:
        idle_bitmap_file = self._get_idle_bitmap_file()
        offset, mask = idle_bitmap_offset_and_mask(pfn)
        idle_bitmap_file.seek(offset)
        raw = idle_bitmap_file.read(8)
        if len(raw) != 8:
            raise OSError(f"short read from idle bitmap for pfn={pfn}")
        return bool(int.from_bytes(raw, "little") & mask)

    def _mark_idle_pfns(self, pfns: Iterable[int]) -> None:
        idle_bitmap_file = self._get_idle_bitmap_file()
        grouped_masks = dict[int, int]()
        for pfn in pfns:
            offset, mask = idle_bitmap_offset_and_mask(pfn)
            grouped_masks[offset] = grouped_masks.get(offset, 0) | mask

        for offset, mask in sorted(grouped_masks.items()):
            idle_bitmap_file.seek(offset)
            idle_bitmap_file.write(mask.to_bytes(8, "little"))
        idle_bitmap_file.flush()
