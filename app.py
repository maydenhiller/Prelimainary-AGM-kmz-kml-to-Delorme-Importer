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
    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
        preferred = ["doc.kml", "root.kml", "index.kml"]
        names = z.namelist()
        target = next((p for p in preferred if p in names), None)
        if target is None:
            target = next((n for n in names if n.lower().endswith(".kml")), None)
        return z.read(target) if target else None

def parse_coordinates_text(coord_text: str) -> List[Tuple[float, float]]:
    coords = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords

def is_triangle(vertices: List[Tuple[float, float]]) -> bool:
    unique = list({(round(lon, 10), round(lat, 10)) for lon, lat in vertices})
    return len(unique) == 3

def centroid(vertices: List[Tuple[float, float]]) -> Tuple[float, float]:
    if not vertices:
        return (0.0, 0.0)
    lons = [lon for lon, _ in vertices]
    lats = [lat for _, lat in vertices]
    return (sum(lons) / len(lons), sum(lats) / len(lats))

def extract_placemark_point(pm: etree._Element) -> Optional[Tuple[float, float]]:
    el = pm.find(".//kml:Point/kml:coordinates", namespaces=KML_NS)
    if el is not None and el.text:
        coords = parse_coordinates_text(el.text)
        if coords:
            lon, lat = coords[0]
            return (lat, lon)
    return None

def extract_placemark_polygon(pm: etree._Element) -> Optional[Tuple[List[Tuple[float, float]], bool]]:
    el = pm.find(".//kml:Polygon/kml:outerBoundaryIs/kml:LinearRing/kml:coordinates", namespaces=KML_NS)
    if el is not None and el.text:
        vertices = parse_coordinates_text(el.text)
        return (vertices, is_triangle(vertices))
    return None

def extract_name(pm: etree._Element) -> str:
    el = pm.find("./kml:name", namespaces=KML_NS)
    if el is not None and el.text:
        return el.text.strip()
    el2 = pm.find(".//kml:name", namespaces=KML_NS)
    return el2.text.strip() if (el2 is not None and el2.text) else ""

def parse_kml(kml_bytes: bytes) -> pd.DataFrame:
    root = etree.fromstring(kml_bytes)
    placemarks = root.findall(".//kml:Placemark", namespaces=KML_NS)
    rows = []
    for pm in placemarks:
        name = extract_name(pm)
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
                continue
        rows.append({"Latitude": lat, "Longitude": lon, "Name": name, "Symbol": symbol})
    return pd.DataFrame(rows, columns=["Latitude", "Longitude", "Name", "Symbol"])

def dataframe_to_txt(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    buf.write(",".join(df.columns) + "\n")
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

        st.download_button("Download CSV", data=csv_bytes, file_name=CSV_FILENAME, mime="text/csv")
        st.download_button("Download TXT", data=txt_bytes, file_name=TXT_FILENAME, mime="text/plain")

    except Exception as e:
        st.error(f"Error processing file: {e}")

if __name__ == "__main__":
    main()
