"""
XML Exporter Module for ResolveFlow Assistant.
Exports Final Cut Pro XML 1.9 (FCPXML) and Final Cut Pro 7 XML with RFC 3986 compliance.
"""
from src.core.fcpxml_generator import FCPXMLGenerator
from src.utils.path_utils import PosixPathAdapter

# Facade / Alias classes for clean architecture naming
XMLExporter = FCPXMLGenerator
FCPXMLBuilder = FCPXMLGenerator

__all__ = ["XMLExporter", "FCPXMLBuilder", "FCPXMLGenerator", "PosixPathAdapter"]
