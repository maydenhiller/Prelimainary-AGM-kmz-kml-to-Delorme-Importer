# app.py
import io
import zipfile
from typing import List, Tuple, Optional

import streamlit as st
import pandas as pd
from lxml import etree

APP_TITLE = "Preliminary AGM kmz/kml to Delorme Importer"
CSV_FILENAME = "Preliminary AGM locations.csv"
TXT_FILENAME = "Preliminary AGM locations.txt"

KML_NS = {"kml": "http://www.opengis.net/kml/2.2", "gx": "http://www.google.com/kml/ext/2.2"}


def read_kml_from_kmz(kmz_bytes: bytes) -> Optional[bytes]:
    """Extract the first .kml file from a KMZ archive."""
    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
        preferred = ["doc.kml", "root.kml", "index.kml"]
        names = z.namelist()
        target = next((p for p in preferred if p in names), None)
        if target is None:
            target = next((n for n in names if n.lower().endswith(".kml")), None)
        return z.read(target) if target else None


def parse_coordinates_text(coord_text: str) -> List[Tuple[float, float]]:
    """
    Parse KML coordinates string into list of (lon, lat).
    KML coordinates are 'lon,lat[,alt]' separated by spaces/newlines.
    """
    coords = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def is_triangle(vertices: List[Tuple[float, float]]) -> bool:
    """Determine if a polygon is a triangle based on unique vertex count."""
    unique = list({(round(lon, 10), round(lat, 10)) for lon, lat in vertices})
    # Many KML polygons repeat the first vertex at the end; unique filtering handles that.
    return len(unique) == 3


def centroid(vertices: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Approximate centroid as arithmetic mean of vertices."""
    if not vertices:
        return (0.0, 0.0)
    lons = [lon for lon, _ in vertices]
    lats = [lat for _, lat in vertices]
    return (sum(lons) / len(lons), sum(lats) / len(lats))


def extract_placemark_point(pm: etree._Element) -> Optional[Tuple[float, float]]:
    """Get Point coordinates from a Placemark, if present."""
    el = pm.find(".//kml:Point/kml:coordinates", namespaces=KML_NS)
    if el is not None and el.text:
        coords = parse_coordinates_text(el.text)
        if coords:
            lon, lat = coords[0]
            return (lat, lon)
    return None


def extract_placemark_polygon(pm: etree._Element) -> Optional[List[Tuple[float, float]]]:
    """Get Polygon outer ring vertices from a Placemark, if present."""
    el = pm.find(
        ".//kml:Polygon/kml:outerBoundaryIs/kml:LinearRing/kml:coordinates",
        namespaces=KML_NS,
    )
    if el is not None and el.text:
        return parse_coordinates_text(el.text)
    return None


def extract_name(pm: etree._Element) -> str:
    """Extract Placemark name text."""
    el = pm.find("./kml:name", namespaces=KML_NS)
    if el is not None and el.text:
        return el.text.strip()
    el2 = pm.find(".//kml:name", namespaces=KML_NS)
    return el2.text.strip() if (el2 is not None and el2.text) else ""


def build_style_index(root: etree._Element):
    """
    Build indices of Style and StyleMap to resolve icon hrefs from styleUrl.
    Returns:
      - icon_by_style_id: dict[str, str] mapping style id -> icon href
      - normal_target_by_stylemap_id: dict[str, str] mapping styleMap id -> target style id (normal)
    """
    icon_by_style_id = {}
    normal_target_by_stylemap_id = {}

    # Index <Style id="..."><IconStyle><Icon><href>...</href>
    for style in root.findall(".//kml:Style", namespaces=KML_NS):
        style_id = style.get("id") or ""
        href_el = style.find(".//kml:IconStyle/kml:Icon/kml:href", namespaces=KML_NS)
        if style_id and href_el is not None and href_el.text:
            icon_by_style_id[style_id] = href_el.text.strip()

    # Index <StyleMap id="..."><Pair><key>normal</key><styleUrl>#styleId</styleUrl>
    for sm in root.findall(".//kml:StyleMap", namespaces=KML_NS):
        sm_id = sm.get("id") or ""
        for pair in sm.findall("./kml:Pair", namespaces=KML_NS):
            key_el = pair.find("./kml:key", namespaces=KML_NS)
            url_el = pair.find("./kml:styleUrl", namespaces=KML_NS)
            if (
                sm_id
                and key_el is not None
                and key_el.text
                and key_el.text.strip().lower() == "normal"
                and url_el is not None
                and url_el.text
            ):
                target = url_el.text.strip()
                # styleUrl usually like "#styleId"
                if target.startswith("#"):
                    target = target[1:]
                normal_target_by_stylemap_id[sm_id] = target

    return icon_by_style_id, normal_target_by_stylemap_id


def resolve_style_href_for_placemark(pm: etree._Element, icon_by_style_id, normal_target_by_stylemap_id) -> Optional[str]:
    """
    Resolve the icon href for a Placemark via its styleUrl, consulting StyleMap if needed.
    """
    url_el = pm.find("./kml:styleUrl", namespaces=KML_NS)
    if url_el is None or not url_el.text:
        return None
    style_ref = url_el.text.strip()
    # If URL references a local style id (e.g., "#0_0"), strip '#'
    if style_ref.startswith("#"):
        style_ref = style_ref[1:]

    # If the ref is a StyleMap id, resolve to its 'normal' style id
    if style_ref in normal_target_by_stylemap_id:
        style_ref = normal_target_by_stylemap_id[style_ref]

    # Finally, look up the icon href for the resolved style id
    href = icon_by_style_id.get(style_ref)
    return href


def detect_symbol(pm: etree._Element, icon_by_style_id, normal_target_by_stylemap_id) -> str:
    """
    Decide symbol:
    - If Polygon with exactly 3 unique vertices -> Purple Triangle.
    - Else if style resolves to an icon href containing 'triangle' -> Purple Triangle.
    - Else -> Red Flag.
    """
    # Geometry-based detection first
    vertices = extract_placemark_polygon(pm)
    if vertices and is_triangle(vertices):
        return "Purple Triangle"

    # Style-based detection
    href = resolve_style_href_for_placemark(pm, icon_by_style_id, normal_target_by_stylemap_id)
    if href and ("triangle" in href.lower()):
        return "Purple Triangle"

    # Default
    return "Red Flag"


def parse_kml(kml_bytes: bytes) -> pd.DataFrame:
    """
    Parse KML bytes and return DataFrame with columns:
    Latitude, Longitude, Name, Symbol
    """
    parser = etree.XMLParser(recover=True)  # robust to imperfect KMLs
    root = etree.fromstring(kml_bytes, parser=parser)

    icon_by_style_id, normal_target_by_stylemap_id = build_style_index(root)
    placemarks = root.findall(".//kml:Placemark", namespaces=KML_NS)

    rows = []
    for pm in placemarks:
        name = extract_name(pm)

        # Prefer Point geometry for coordinates
        point = extract_placemark_point(pm)
        if point:
            lat, lon = point
        else:
            # Fallback to polygon centroid if no point
            vertices = extract_placemark_polygon(pm)
            if vertices:
                c_lon, c_lat = centroid(vertices)
                lat, lon = c_lat, c_lon
            else:
                # No usable geometry -> skip
                continue

        symbol = detect_symbol(pm, icon_by_style_id, normal_target_by_stylemap_id)

        rows.append(
            {
                "Latitude": lat,
                "Longitude": lon,
                "Name": name,
                "Symbol": symbol,
            }
        )

    df = pd.DataFrame(rows, columns=["Latitude", "Longitude", "Name", "Symbol"])
    return df


def dataframe_to_txt(df: pd.DataFrame) -> bytes:
    """
    Create a .txt representation mirroring the CSV content (comma-separated).
    """
    buf = io.StringIO()
    buf.write(",".join(df.columns) + "\n")
    for _, row in df.iterrows():
        buf.write(f'{row["Latitude"]},{row["Longitude"]},{row["Name"]},{row["Symbol"]}\n')
    return buf.getvalue().encode("utf-8")


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Standard CSV export; Excel may render leading zeros as numbers, which is acceptable per request."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def main():
    st.set_page_config(page_title=APP_TITLE, layout="centered")
    st.title(APP_TITLE)
    st.caption("Upload a KML or KMZ. The app will detect Placemark features and export Preliminary AGM locations as CSV and TXT.")

    uploaded = st.file_uploader("Upload KMZ or KML", type=["kmz", "kml"])

    if uploaded is None:
        st.info("Awaiting file upload.")
        return

    try:
        if uploaded.name.lower().endswith(".kmz"):
            kml_bytes = read_kml_from_kmz(uploaded.read())
            if kml_bytes is None:
                st.error("No KML file found inside the KMZ.")
                return
        else:
            kml_bytes = uploaded.read()

        df = parse_kml(kml_bytes)

        if df.empty:
            st.warning("No Placemark features with usable geometries were found.")
            return

        st.subheader("Detected placemarks")
        st.dataframe(df, use_container_width=True)

        csv_bytes = dataframe_to_csv_bytes(df)
        txt_bytes = dataframe_to_txt(df)

        # Single download containing both CSV and TXT
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr(CSV_FILENAME, csv_bytes)
            zf.writestr(TXT_FILENAME, txt_bytes)
        zip_buffer.seek(0)

        st.download_button(
            label="Download CSV + TXT (zipped)",
            data=zip_buffer,
            file_name="Preliminary_AGM_locations.zip",
            mime="application/zip",
        )

    except Exception as e:
        st.error(f"Error processing file: {e}")


if __name__ == "__main__":
    main()
