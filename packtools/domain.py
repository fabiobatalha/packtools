# coding:utf-8
"""The domain-specific adapters.

An adapter is an object that provides domain-specific apis for another object,
called adaptee.

Examples are: XMLValidator and XMLPacker objects, which provide SciELO PS
validation behaviour and packaging functionality, respectively.
"""
from __future__ import unicode_literals
import logging
from copy import deepcopy
import os

from lxml import etree, isoschematron

from . import utils, catalogs, checks, style_errors, exceptions


__all__ = ['XMLValidator',]


LOGGER = logging.getLogger(__name__)


# As a general rule, only the latest 2 versions are supported simultaneously.
CURRENTLY_SUPPORTED_VERSIONS = os.environ.get(
    'PACKTOOLS_SUPPORTED_SPS_VERSIONS', 'sps-1.2:sps-1.3').split(':')

ALLOWED_PUBLIC_IDS = (
    '-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.0 20120330//EN',
)

# doctype public ids for sps <= 1.1
ALLOWED_PUBLIC_IDS_LEGACY = (
    '-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.0 20120330//EN',
    '-//NLM//DTD Journal Publishing DTD v3.0 20080202//EN',
)


def _get_public_ids(sps_version):
    """Returns the set of allowed public ids for the XML based on its version.
    """
    if sps_version in ['pre-sps', 'sps-1.1']:
        return frozenset(ALLOWED_PUBLIC_IDS_LEGACY)
    else:
        return frozenset(ALLOWED_PUBLIC_IDS)


def _init_sps_version(xml_et, supported_versions=None):
    """Returns the SPS spec version for `xml_et` or raises ValueError.

    It also checks if the version is currently supported.

    :param xml_et: etree instance.
    :param supported_versions: (optional) the default value is set by env var `PACKTOOLS_SUPPORTED_SPS_VERSIONS`.
    """
    if supported_versions is None:
        supported_versions = CURRENTLY_SUPPORTED_VERSIONS

    try:
        version_from_xml = xml_et.getroot().attrib['specific-use']
    except KeyError:
        raise exceptions.XMLSPSVersionError('Missing SPS version at /article/@specific-use')

    if version_from_xml not in supported_versions:
        raise exceptions.XMLSPSVersionError('%s is not currently supported' % version_from_xml)
    else:
        return version_from_xml


def Schematron(file):
    with open(file, mode='rb') as fp:
        xmlschema_doc = etree.parse(fp)

    return isoschematron.Schematron(xmlschema_doc)


def StdSchematron(schema_name):
    """Returns an instance of `isoschematron.Schematron`.

    A standard schematron is one bundled with packtools.
    The returned instance is cached due to performance reasons.

    :param schema_name: The logical name of schematron file in the package `catalogs`.
    """
    cache = utils.setdefault(StdSchematron, 'cache', lambda: {})

    if schema_name in cache:
        return cache[schema_name]
    else:
        try:
            schematron = Schematron(catalogs.SCHEMAS[schema_name])
        except KeyError:
            raise ValueError('Unknown schema %s' % (schema_name,))

        cache[schema_name] = schematron
        return schematron


def XSLT(xslt_name):
    """Returns an instance of `etree.XSLT`.

    The returned instance is cached due to performance reasons.
    """
    cache = utils.setdefault(XSLT, 'cache', lambda: {})

    if xslt_name in cache:
        return cache[xslt_name]
    else:
        try:
            xslt_doc = etree.parse(catalogs.XSLTS[xslt_name])
        except KeyError:
            raise ValueError('Unknown xslt %s' % (xslt_name,))

        xslt = etree.XSLT(xslt_doc)
        cache[xslt_name] = xslt
        return xslt


# --------------------------------
# adapters for etree._ElementTree
# --------------------------------
def XMLValidator(file, dtd=None, no_doctype=False, sps_version=None,
             extra_schematron=None, supported_sps_versions=None):
    """Factory of _XMLValidator instances, to perform SPS validations.

    If `file` is not an etree instance, it will be parsed using
    :func:`XML`.

    SPS validation stages are:
      - JATS 1.0 or PMC 3.0 (as bound by the doctype declaration or passed
        explicitly)
      - SciELO Style - ISO Schematron
      - SciELO Style - Python based pipeline

    If the DOCTYPE is declared, its public id is validated against a white list,
    declared by ``allowed_public_ids`` class variable. The system id is ignored.
    By default, the allowed values are:

      - SciELO PS >= 1.2:
        - ``-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.0 20120330//EN``
      - SciELO PS 1.1:
        - ``-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.0 20120330//EN``
        - ``-//NLM//DTD Journal Publishing DTD v3.0 20080202//EN``

    :param file: Path to the XML file, URL, etree or file-object.
    :param dtd: (optional) etree.DTD instance. If not provided, we try the external DTD.
    :param no_doctype: (optional) if missing DOCTYPE declaration is accepted.
    :param sps_version: (optional) force the style validation against a SPS version.
    :param extra_schematron: (optional) extra schematron schema.
    :param supported_sps_versions: (optional) list of supported versions. the
           only way to bypass this restriction is by using the arg `sps_version`.
    """
    if isinstance(file, etree._ElementTree):
        et = file
    else:
        et = utils.XML(file)

    # can raise exception
    sps_version = sps_version or _init_sps_version(et, supported_sps_versions)

    allowed_public_ids = _get_public_ids(sps_version)

    # DOCTYPE declaration must be present by default. This behaviour can
    # be changed by the `no_doctype` arg.
    doctype = et.docinfo.doctype
    if not doctype and not no_doctype:
        raise exceptions.XMLDoctypeError('Missing DOCTYPE declaration')

    # if there exists a DOCTYPE declaration, ensure its PUBLIC-ID is
    # supported.
    public_id = et.docinfo.public_id
    if doctype and public_id not in allowed_public_ids:
        raise exceptions.XMLDoctypeError('Unsuported DOCTYPE public id')

    return _XMLValidator(et, sps_version, dtd, extra_schematron)


#--------------------------------
# adapters for etree._ElementTree
#--------------------------------
class _XMLValidator(object):
    """Adapter that performs SPS validations.

    SPS validation stages are:
      - JATS 1.0 or PMC 3.0 (as bound by the doctype declaration or passed
        explicitly)
      - SciELO Style - ISO Schematron
      - SciELO Style - Python based pipeline

    :param file: etree._ElementTree instance.
    :param sps_version: the version of the SPS that will be the basis for validation.
    :param dtd: (optional) etree.DTD instance. If not provided, we try the external DTD.
    :param extra_schematron: (optional) extra schematron schema.
    """
    def __init__(self, file, sps_version, dtd=None, extra_schematron=None):
        assert isinstance(file, etree._ElementTree)

        self.lxml = file
        self.sps_version = sps_version
        self.allowed_public_ids = _get_public_ids(self.sps_version)
        self.doctype = self.lxml.docinfo.doctype
        self.dtd = dtd or self.lxml.docinfo.externalDTD
        self.source_url = self.lxml.docinfo.URL
        self.public_id = self.lxml.docinfo.public_id

        # Load schematron schema based on sps version. Can raise ValueError
        self.schematron = StdSchematron(self.sps_version)

        # Load user-provided schematron schema
        if extra_schematron:
            self.extra_schematron = Schematron(extra_schematron)
        else:
            self.extra_schematron = None

        # Load python based validation pipeline
        self.ppl = checks.StyleCheckingPipeline()

    def validate(self):
        """Validate the source XML against JATS DTD.

        Returns a tuple comprising the validation status and the errors list.
        """
        if self.dtd is None:
            raise exceptions.UndefinedDTDError('The DTD/XSD could not be loaded')

        def make_error_log():
            return [style_errors.SchemaStyleError(err) for err in self.dtd.error_log]

        result = self.dtd.validate(self.lxml)
        errors = make_error_log()
        return result, errors

    def _validate_sch(self):
        """Validate the source XML against SPS-Style Schematron.

        Returns a tuple comprising the validation status and the errors list.
        """
        def make_error_log(schematron):
            err_log = schematron.error_log
            return [style_errors.SchematronStyleError(err) for err in err_log]

        result = self.schematron.validate(self.lxml)
        errors = make_error_log(self.schematron)

        if self.extra_schematron:
            extra_result = self.extra_schematron.validate(self.lxml)  # run
            result = result and extra_result
            errors += make_error_log(self.extra_schematron)

        return result, errors

    def validate_style(self):
        """Validate the source XML against SPS-Style Tagging guidelines.

        Returns a tuple comprising the validation status and the errors list.
        """
        def make_error_log():
            errors = next(self.ppl.run(self.lxml, rewrap=True))
            errors += self._validate_sch()[1]
            return errors

        errors = make_error_log()
        result = not bool(errors)
        return result, errors

    def validate_all(self, fail_fast=False):
        """Runs all validations.

        First, the XML is validated against the DTD (calling :meth:`validate`).
        If no DTD is provided and the argument ``fail_fast == True``, a ``TypeError``
        is raised. After that, the XML is validated against the SciELO style
        (calling :meth:`validate_style`).

        :param fail_fast: (optional) raise ``TypeError`` if the DTD has not been loaded.
        """
        try:
            v_result, v_errors = self.validate()

        except exceptions.UndefinedDTDError:
            if fail_fast:
                raise
            else:
                v_result = None
                v_errors = []

        s_result, s_errors = self.validate_style()

        val_status = False if s_result is False else v_result
        val_errors = v_errors + s_errors

        return val_status, val_errors

    def _annotate_error(self, element, error):
        """Add an annotation prior to `element`, with `error` as the content.

        The annotation is a comment added prior to `element`.

        :param element: etree instance to be annotated.
        :param error: string of the error.
        """
        notice_element = etree.Element('SPS-ERROR')
        notice_element.text = error
        element.addprevious(etree.Comment('SPS-ERROR: %s' % error))

    def annotate_errors(self, fail_fast=False):
        """Add notes on all elements that have errors.

        The errors list is generated as the result of calling :meth:`validate_all`.
        """
        status, errors = self.validate_all(fail_fast=fail_fast)
        mutating_xml = deepcopy(self.lxml)

        if status is True:
            return mutating_xml

        err_pairs = []
        for error in errors:
            try:
                err_element = error.get_apparent_element(mutating_xml)
            except ValueError:
                LOGGER.info('Could not locate the element name in: %s', error.message)
                err_element = mutating_xml.getroot()

            err_pairs.append((err_element, error.message))

        for el, em in err_pairs:
            self._annotate_error(el, em)

        return mutating_xml

    def __str__(self):
        return etree.tostring(self.lxml, pretty_print=True,
            encoding='utf-8', xml_declaration=True)

    def __unicode__(self):
        return str(self).decode('utf-8')

    def __repr__(self):
        try:
            is_valid = self.validate_all()[0]
        except exceptions.UndefinedDTDError:
            is_valid = None

        return '<%s xml=%s valid=%s>' % (self.__class__.__name__, self.lxml, is_valid)

    def read(self):
        """Read the XML contents as text.
        """
        return unicode(self)

    @property
    def meta(self):
        """Article metadata.
        """
        parsed_xml = self.lxml

        xml_nodes = {
            "journal_title": "front/journal-meta/journal-title-group/journal-title",
            "journal_eissn": "front/journal-meta/issn[@pub-type='epub']",
            "journal_pissn": "front/journal-meta/issn[@pub-type='ppub']",
            "article_title": "front/article-meta/title-group/article-title",
            "issue_year": "front/article-meta/pub-date/year",
            "issue_volume": "front/article-meta/volume",
            "issue_number": "front/article-meta/issue",
        }

        metadata = {'filename': self.source_url}
        for node_k, node_v in xml_nodes.items():
            node = parsed_xml.find(node_v)
            metadata[node_k] = getattr(node, 'text', None)

        return metadata

    @property
    def assets(self):
        """Lists all static assets referenced by the XML.
        """
        return utils.get_static_assets(self.lxml)

    def lookup_assets(self, base_dir):
        """Look for each asset in `base_dir`, and returns a list of tuples
        with the asset name and its presence status.

        :param base_dir: path to the directory where the lookup will be based on.
        """
        is_available = utils.make_file_checker(base_dir)
        return [(asset, is_available(asset)) for asset in self.assets]

