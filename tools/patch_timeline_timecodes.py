#!/usr/bin/env python3
"""
Script chuẩn hóa Timecode và Reel Name cho FCPXML / FCP7 XML trong _TIMELINE_IMPORT.
Khôi phục triệt để lỗi Media Offline (đỏ clip / 35 clips not found) trên DaVinci Resolve.
"""
import os
import sys
import re
import html
import urllib.parse
from pathlib import Path
import xml.etree.ElementTree as ET

# Add project root to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from src.core.fcpxml_generator import FCPXMLGenerator
from src.core.validator import DryRunValidator


def parse_frames_from_time_str(time_str: str, default_fps: int = 30) -> int:
    """Chuyển chuỗi thời gian FCPXML (ví dụ '1776663/30s' hoặc '0s' hoặc '10s') thành frame count."""
    if not time_str or time_str == "0s" or time_str == "0":
        return 0
    clean = time_str.rstrip("s")
    if "/" in clean:
        parts = clean.split("/")
        num = int(parts[0])
        den = int(parts[1])
        return int(round(num * default_fps / den))
    else:
        try:
            return int(round(float(clean) * default_fps))
        except ValueError:
            return 0


def patch_fcpxml(fcpxml_path: str, target_fps: int = 30) -> bool:
    print(f"🔧 Đang vá FCPXML: {fcpxml_path} ...")
    tree = ET.parse(fcpxml_path)
    root = tree.getroot()

    resources = root.find("resources")
    if resources is None:
        print("❌ Không tìm thấy <resources> trong FCPXML.")
        return False

    # 1. Trích xuất timecode gốc và cập nhật <asset>
    asset_tc_map = {}  # asset_id -> start_tc_frames
    asset_file_map = {}  # asset_id -> real_path
    asset_reel_map = {}  # asset_id -> reel_name
    orig_asset_start_map = {}  # asset_id -> original start frame in XML

    for asset in resources.findall("asset"):
        asset_id = asset.attrib.get("id")
        src = asset.attrib.get("src")
        local_path = DryRunValidator._uri_to_local_path(src)
        if not local_path or not os.path.exists(local_path):
            print(f"⚠ Không tìm thấy file cục bộ cho asset {asset_id}: {src}")
            continue

        base_name = os.path.basename(local_path)
        reel_name = os.path.splitext(base_name)[0]
        has_video = asset.attrib.get("hasVideo") == "1"

        orig_start_str = asset.attrib.get("start", "0s")
        orig_start_f = parse_frames_from_time_str(orig_start_str, target_fps)
        orig_asset_start_map[asset_id] = orig_start_f

        if has_video:
            tc_frames, tc_str, tc_fmt = FCPXMLGenerator.get_clip_start_frame_and_tc(local_path, target_fps)
        else:
            tc_frames, tc_str, tc_fmt = 0, "00:00:00:00", "NDF"

        asset_tc_map[asset_id] = tc_frames
        asset_file_map[asset_id] = local_path
        asset_reel_map[asset_id] = reel_name

        # Cập nhật start attribute
        asset.attrib["start"] = f"{tc_frames}/{target_fps}s"
        asset.attrib["format"] = "r_fmt"

        # Thêm/Cập nhật Reel Name trong <metadata>
        md_container = asset.find("metadata")
        if md_container is None:
            md_container = ET.SubElement(asset, "metadata")
        
        has_reel = False
        for md in md_container.findall("md"):
            if md.attrib.get("key") == "com.apple.proapps.studio.reel":
                md.attrib["value"] = reel_name
                has_reel = True
                break
        if not has_reel:
            reel_el = ET.SubElement(md_container, "md")
            reel_el.attrib["key"] = "com.apple.proapps.studio.reel"
            reel_el.attrib["value"] = reel_name

        print(f"   ✔ Asset '{base_name}': start={tc_frames}/{target_fps}s ({tc_str}), reel={reel_name}")

    # 2. Cập nhật các <asset-clip> trên <spine>
    spine = root.find(".//spine")
    if spine is None:
        print("❌ Không tìm thấy <spine> trong FCPXML.")
        return False

    for clip in spine.findall("asset-clip"):
        ref = clip.attrib.get("ref")
        start_tc_f = asset_tc_map.get(ref, 0)
        orig_start_f = orig_asset_start_map.get(ref, 0)
        reel_name = asset_reel_map.get(ref, "")

        old_start_str = clip.attrib.get("start", "0s")
        current_clip_start = parse_frames_from_time_str(old_start_str, target_fps)
        rel_in_f = max(0, current_clip_start - orig_start_f)
        new_start_f = start_tc_f + rel_in_f

        clip.attrib["start"] = f"{new_start_f}/{target_fps}s"
        clip.attrib["format"] = "r_fmt"
        clip.attrib["audioRole"] = "dialogue"

        # Thêm/Cập nhật Reel Name trong <metadata>
        if reel_name:
            md_container = clip.find("metadata")
            if md_container is None:
                md_container = ET.Element("metadata")
                clip.insert(0, md_container)

            has_reel = False
            for md in md_container.findall("md"):
                if md.attrib.get("key") == "com.apple.proapps.studio.reel":
                    md.attrib["value"] = reel_name
                    has_reel = True
                    break
            if not has_reel:
                reel_el = ET.SubElement(md_container, "md")
                reel_el.attrib["key"] = "com.apple.proapps.studio.reel"
                reel_el.attrib["value"] = reel_name

        # Dời mốc title phụ đề và marker tương ứng với camera start timecode chênh lệch
        delta_tc = start_tc_f - orig_start_f
        if delta_tc != 0:
            for title in clip.findall(".//title"):
                t_off_str = title.attrib.get("offset")
                if t_off_str:
                    t_off_f = parse_frames_from_time_str(t_off_str, target_fps)
                    title.attrib["offset"] = f"{t_off_f + delta_tc}/{target_fps}s"

            for marker in clip.findall(".//marker"):
                m_start_str = marker.attrib.get("start")
                if m_start_str:
                    m_start_f = parse_frames_from_time_str(m_start_str, target_fps)
                    marker.attrib["start"] = f"{m_start_f + delta_tc}/{target_fps}s"

    tree.write(fcpxml_path, encoding="utf-8", xml_declaration=True)
    print(f"🎉 Đã vá FCPXML thành công: {fcpxml_path}")
    return True


def patch_fcp7_xml(xml_path: str, target_fps: int = 30) -> bool:
    print(f"🔧 Đang vá FCP7 XML: {xml_path} ...")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Lưu cache timecode theo pathurl
    file_tc_map = {}
    orig_file_tc_map = {}

    for file_node in root.findall(".//file"):
        pathurl_el = file_node.find("pathurl")
        if pathurl_el is None or not pathurl_el.text:
            continue
        purl = pathurl_el.text
        if purl in file_tc_map:
            tc_frames, tc_str, tc_fmt = file_tc_map[purl]
        else:
            local_p = DryRunValidator._uri_to_local_path(purl)
            if local_p and os.path.exists(local_p):
                tc_frames, tc_str, tc_fmt = FCPXMLGenerator.get_clip_start_frame_and_tc(local_p, target_fps)
            else:
                tc_frames, tc_str, tc_fmt = 0, "00:00:00:00", "NDF"
            file_tc_map[purl] = (tc_frames, tc_str, tc_fmt)

        orig_tc_f = 0
        tc_node = file_node.find("timecode")
        if tc_node is not None:
            frame_node = tc_node.find("frame")
            if frame_node is not None:
                orig_tc_f = int(frame_node.text)
                frame_node.text = str(tc_frames)
            str_node = tc_node.find("string")
            if str_node is not None:
                str_node.text = tc_str
            df_node = tc_node.find("displayformat")
            if df_node is not None:
                df_node.text = tc_fmt
        orig_file_tc_map[purl] = orig_tc_f

    # Cập nhật <in> và <out> trên từng <clipitem>
    for clipitem in root.findall(".//clipitem"):
        file_el = clipitem.find("file")
        if file_el is None:
            continue
        purl_el = file_el.find("pathurl")
        if purl_el is None or not purl_el.text:
            # File node dedup: tìm file gốc theo id
            f_id = file_el.attrib.get("id")
            orig = root.find(f".//file[@id='{f_id}'][pathurl]")
            purl = orig.find("pathurl").text if orig is not None and orig.find("pathurl") is not None else None
        else:
            purl = purl_el.text

        if not purl or purl not in file_tc_map:
            continue

        tc_frames = file_tc_map[purl][0]
        orig_tc_f = orig_file_tc_map.get(purl, 0)
        delta_tc = tc_frames - orig_tc_f
        if delta_tc != 0:
            in_el = clipitem.find("in")
            out_el = clipitem.find("out")
            if in_el is not None and out_el is not None:
                in_el.text = str(int(in_el.text) + delta_tc)
                out_el.text = str(int(out_el.text) + delta_tc)

    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    print(f"🎉 Đã vá FCP7 XML thành công: {xml_path}")
    return True


if __name__ == "__main__":
    fcpxml_file = "/media/vanntruongg/Ventoy/ResolveFlow_H264/_TIMELINE_IMPORT/ResolveFlow_H264_Copilot_Cut.fcpxml"
    fcp7xml_file = "/media/vanntruongg/Ventoy/ResolveFlow_H264/_TIMELINE_IMPORT/ResolveFlow_H264_Copilot_Cut.xml"
    if os.path.exists(fcpxml_file):
        patch_fcpxml(fcpxml_file, 30)
    if os.path.exists(fcp7xml_file):
        patch_fcp7_xml(fcp7xml_file, 30)
