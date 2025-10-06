# app.py
import io
import zipfile
from typing import List, Tuple, Optional

import streamlit as st
import pandas as pd
from lxml import etree

APP_TITLE = "Prelimainary AGM kmz/kml to Delorme Importer"
CSV_FILENAME = "Preliminary AGM locations.csv"
TXT_FILENAME = "Preliminary AGM locations.txt"

KML_NS = {"kml": "http://www.opengis.net/kml/2.2", "gx": "http://www.google.com/kml/ext/2.2"}

def read_kml_from_kmz(kmz_bytes: bytes) -> Optional[bytes]:
    """Extract the first .kml file from a KMZ archive."""
    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
        # Prefer common names, fallback to first .kml found.
        preferred = ["doc.kml", "root.kml", "index.kml"]
        names = z.namelist()
        target = None
        for p in preferred:
            if p in names:
                target = p
                break
        if target is None:
            for n in names:
                if n.lower().endswith(".kml"):
                    target = n
                    break
        if target is None:
            return None
        return z.read(target)

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
                # Skip malformed entries
                continue
    return coords

def is_triangle(vertices: List[Tuple[float, float]]) -> bool:
    """Determine if a polygon is a triangle based on unique vertex count."""
    unique = list({(round(lon, 10), round(lat, 10)) for lon, lat in vertices})
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
    point_coords_el = pm.find(".//kml:Point/kml:coordinates", namespaces=KML_NS)
    if point_coords_el is not None and point_coords_el.text:
        coords = parse_coordinates_text(point_coords_el.text)
        if coords:
            lon, lat = coords[0]
            return (lat, lon)
    return None

def extract_placemark_polygon(pm: etree._Element) -> Optional[Tuple[List[Tuple[float, float]], bool]]:
    """Get Polygon vertices and triangle flag from a Placemark, if present."""
    poly_coords_el = pm.find(
        ".//kml:Polygon/kml:outerBoundaryIs/kml:LinearRing/kml:coordinates",
        namespaces=KML_NS,
    )
    if poly_coords_el is not None and poly_coords_el.text:
        vertices = parse_coordinates_text(poly_coords_el.text)
        tri = is_triangle(vertices)
        return (vertices, tri)
    return None

def extract_name(pm: etree._Element) -> str:
    name_el = pm.find("./kml:name", namespaces=KML_NS)
    if name_el is not None and name_el.text:
        return name_el.text.strip()
    # Sometimes name is nested
    name_el2 = pm.find(".//kml:name", namespaces=KML_NS)
    return name_el2.text.strip() if (name_el2 is not None and name_el2.text) else ""

def parse_kml(kml_bytes: bytes) -> pd.DataFrame:
    """
    Parse KML bytes and return DataFrame with columns:
    Latitude, Longitude, Name, Symbol
    """
    root = etree.fromstring(kml_bytes)
    placemarks = root.findall(".//kml:Placemark", namespaces=KML_NS)

    rows = []
    for pm in placemarks:
        name = extract_name(pm)

        # Prefer Point geometry if available
        point = extract_placemark_point(pm)
        if point:
            lat, lon = point
            symbol = "Yellow Dot"
        else:
            poly = extract_placemark_polygon(pm)
            if poly:
                vertices, tri = poly
                c_lon, c_lat = centroid(vertices)
                lat, lon = c_lat, c_lon
                symbol = "Purple Triangle" if tri else "Yellow Dot"
            else:
                # No usable geometry -> skip
                continue

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
    # Write header
    buf.write(",".join(df.columns) + "\n")
    # Write rows
    for _, row in df.iterrows():
        buf.write(f'{row["Latitude"]},{row["Longitude"]},{row["Name"]},{row["Symbol"]}\n')
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

        csv_bytes = df.to_csv(index=False).encode("utf-8")
        txt_bytes = dataframe_to_txt(df)

        st.download_button(
            label=f"Download CSV ({CSV_FILENAME})",
            data=csv_bytes,
            file_name=CSV_FILENAME,
            mime="text/csv",
        )
        st.download_button(
            label=f"Download TXT ({TXT_FILENAME})",
            data=txt_bytes,
            file_name=TXT_FILENAME,
            mime="text/plain",
        )

    except Exception as e:
        st.error(f"Error processing file: {e}")

if __name__ == "__main__":
    main()
