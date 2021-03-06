# Copyright 2017 AT&T Intellectual Property.  All other rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import traceback
import yaml

import falcon
from oslo_log import log as logging
import six

LOG = logging.getLogger(__name__)


def get_version_from_request(req):
    """Attempt to extract the API version string."""
    for part in req.path.split('/'):
        if '.' in part and part.startswith('v'):
            return part
    return 'N/A'


def format_error_resp(req,
                      resp,
                      status_code=falcon.HTTP_500,
                      message="",
                      reason="",
                      error_type=None,
                      error_list=None,
                      info_list=None):
    """Generate a error message body and throw a Falcon exception to trigger
    an HTTP status.

    :param req: ``falcon`` request object.
    :param resp: ``falcon`` response object to update.
    :param status_code: ``falcon`` status_code constant.
    :param message: Optional error message to include in the body.
                    This should be the summary level of the error
                    message, encompassing an overall result. If
                    no other messages are passed in the error_list,
                    this message will be repeated in a generated
                    message for the output message_list.
    :param reason: Optional reason code to include in the body
    :param error_type: If specified, the error type will be used;
                       otherwise, this will be set to
                       'Unspecified Exception'.
    :param error_list: optional list of error dictionaries. Minimally,
                       the dictionary will contain the 'message' field,
                       but should also contain 'error': ``True``.
    :param info_list: optional list of info message dictionaries.
                      Minimally, the dictionary needs to contain a
                      'message' field, but should also have a
                      'error': ``False`` field.
    """

    if error_type is None:
        error_type = 'Unspecified Exception'

    # Since we're handling errors here, if error list is None, set up a default
    # error item. If we have info items, add them to the message list as well.
    # In both cases, if the error flag is not set, set it appropriately.
    if error_list is None:
        error_list = [{'message': 'An error occurred, but was not specified',
                       'error': True}]
    else:
        for error_item in error_list:
            if 'error' not in error_item:
                error_item['error'] = True

    if info_list is None:
        info_list = []
    else:
        for info_item in info_list:
            if 'error' not in info_item:
                info_item['error'] = False

    message_list = error_list + info_list

    error_response = {
        'kind': 'status',
        'apiVersion': get_version_from_request(req),
        'metadata': {},
        'status': 'Failure',
        'message': message,
        'reason': reason,
        'details': {
            'errorType': error_type,
            'errorCount': len(error_list),
            'messageList': message_list
        },
        'code': status_code,
        # TODO(fmontei): Make this class-specific later. For now, retry
        # is set to True only for internal server errors.
        'retry': True if status_code is falcon.HTTP_500 else False
    }

    resp.body = yaml.safe_dump(error_response)
    resp.status = status_code


def default_exception_handler(ex, req, resp, params):
    """Catch-all execption handler for standardized output.

    If this is a standard falcon HTTPError, rethrow it for handling by
    ``default_exception_serializer`` below.
    """
    if isinstance(ex, falcon.HTTPError):
        # Allow the falcon http errors to bubble up and get handled.
        raise ex
    else:
        # Take care of the uncaught stuff.
        exc_string = traceback.format_exc()
        LOG.error('Unhanded Exception being handled: \n%s', exc_string)
        format_error_resp(
            req,
            resp,
            error_type=ex.__class__.__name__,
            message="Unhandled Exception raised: %s" % six.text_type(ex)
        )


def default_exception_serializer(req, resp, exception):
    """Serializes instances of :class:`falcon.HTTPError` into YAML format and
    formats the error body so it adheres to the UCP error formatting standard.
    """
    format_error_resp(
        req,
        resp,
        status_code=exception.status,
        # TODO(fmontei): Provide an overall error message instead.
        message=exception.description,
        reason=exception.title,
        error_type=exception.__class__.__name__,
        error_list=[{'message': exception.description, 'error': True}]
    )


class DeckhandException(Exception):
    """Base Deckhand Exception
    To correctly use this class, inherit from it and define
    a 'msg_fmt' property. That msg_fmt will get printf'd
    with the keyword arguments provided to the constructor.
    """
    msg_fmt = "An unknown exception occurred."
    code = 500

    def __init__(self, message=None, **kwargs):
        kwargs.setdefault('code', DeckhandException.code)

        if not message:
            try:
                message = self.msg_fmt % kwargs
            except Exception:
                message = self.msg_fmt

        self.message = message
        super(DeckhandException, self).__init__(message)

    def format_message(self):
        return self.args[0]


class InvalidDocumentFormat(DeckhandException):
    """Schema validations failed for the provided document(s).

    **Troubleshoot:**
    """
    msg_fmt = ("The provided document(s) failed schema validation. Details: "
               "%(details)s")
    code = 400


class InvalidDocumentLayer(DeckhandException):
    """The document layer is invalid.

    **Troubleshoot:**

    * Check that the document layer is contained in the layerOrder in the
      registered LayeringPolicy in the system.
    """
    msg_fmt = ("Invalid layer '%(document_layer)s' for document "
               "[%(document_schema)s] %(document_name)s was not found in "
               "layerOrder: %(layer_order)s for provided LayeringPolicy: "
               "%(layering_policy_name)s.")
    code = 400


class InvalidDocumentParent(DeckhandException):
    """The document parent is invalid.

    **Troubleshoot:**

    * Check that the document `schema` and parent `schema` match.
    * Check that the document layer is lower-order than the parent layer.
    """
    msg_fmt = ("The document parent [%(parent_schema)s] %(parent_name)s is "
               "invalid for document [%(document_schema)s] %(document_name)s. "
               "Reason: %(reason)s")
    code = 400


class IndeterminateDocumentParent(DeckhandException):
    """More than one parent document was found for a document.

    **Troubleshoot:**
    """
    msg_fmt = "Too many parent documents found for document %(document)s."
    code = 400


class SubstitutionDependencyCycle(DeckhandException):
    """An illegal substitution depdencency cycle was detected.

    **Troubleshoot:**

    * Check that there is no two-way substitution dependency between documents.
    """
    msg_fmt = ('Cannot determine substitution order as a dependency '
               'cycle exists for the following documents: %(cycle)s.')
    code = 400


class MissingDocumentKey(DeckhandException):
    """The key does not exist in the "rendered_data".

    **Troubleshoot:**
    """
    msg_fmt = ("Missing document key %(key)s from either parent or child. "
               "Parent: %(parent)s. Child: %(child)s.")
    code = 400


class MissingDocumentPattern(DeckhandException):
    """'Pattern' is not None and data[jsonpath] doesn't exist.

    **Troubleshoot:**
    """
    msg_fmt = ("Missing document pattern %(pattern)s in %(data)s at path "
               "%(path)s.")
    code = 400


class UnsupportedActionMethod(DeckhandException):
    """The action is not in the list of supported methods.

    **Troubleshoot:**
    """
    msg_fmt = ("Method in %(actions)s is invalid for document %(document)s.")
    code = 400


class RevisionTagBadFormat(DeckhandException):
    """The tag data is neither None nor dictionary.

    **Troubleshoot:**
    """
    msg_fmt = ("The requested tag data %(data)s must either be null or "
               "dictionary.")
    code = 400


class DocumentNotFound(DeckhandException):
    """The requested document could not be found.

    **Troubleshoot:**
    """
    msg_fmt = ("The requested document %(document)s was not found.")
    code = 404


class RevisionNotFound(DeckhandException):
    """The revision cannot be found or doesn't exist.

    **Troubleshoot:**
    """
    msg_fmt = "The requested revision %(revision)s was not found."
    code = 404


class RevisionTagNotFound(DeckhandException):
    """The tag for the revision id was not found.

    **Troubleshoot:**
    """
    msg_fmt = ("The requested tag '%(tag)s' for revision %(revision)s was "
               "not found.")
    code = 404


class ValidationNotFound(DeckhandException):
    """The requested validation was not found.

    **Troubleshoot:**
    """
    msg_fmt = ("The requested validation entry %(entry_id)s was not found "
               "for validation name %(validation_name)s and revision ID "
               "%(revision_id)s.")
    code = 404


class DocumentExists(DeckhandException):
    """A document attempted to be put into a bucket where another document with
    the same schema and metadata.name already exist.

    **Troubleshoot:**
    """
    msg_fmt = ("Document with schema %(schema)s and metadata.name "
               "%(name)s already exists in bucket %(bucket)s.")
    code = 409


class SingletonDocumentConflict(DeckhandException):
    """A singleton document already exist within the system.

    **Troubleshoot:**
    """

    msg_fmt = ("A singleton document by the name %(document)s already "
               "exists in the system. The new document %(conflict)s cannot be "
               "created. To create a document with a new name, delete the "
               "current one first.")
    code = 409


class LayeringPolicyNotFound(DeckhandException):
    """Required LayeringPolicy was not found for layering.

    **Troubleshoot:**
    """
    msg_fmt = ("Required LayeringPolicy was not found for layering.")
    code = 409


class SubstitutionSourceNotFound(DeckhandException):
    """Required substitution source document was not found for layering.

    **Troubleshoot:**

    * Ensure that the missing source document being referenced exists in
      the system or was passed to the layering module.
    """
    msg_fmt = (
        "Required substitution source document [%(src_schema)s] %(src_name)s "
        "was not found, yet is referenced by [%(document_schema)s] "
        "%(document_name)s.")
    code = 409


class BarbicanException(DeckhandException):
    """An error occurred with Barbican.

    **Troubleshoot:**
    """

    def __init__(self, message, code):
        super(BarbicanException, self).__init__(message=message, code=code)


class PolicyNotAuthorized(DeckhandException):
    """The policy action is not found in the list of registered rules.

    **Troubleshoot:**
    """
    msg_fmt = "Policy doesn't allow %(action)s to be performed."
    code = 403


class UnknownSubstitutionError(DeckhandException):
    """An unknown error occurred during substitution.

    **Troubleshoot:**
    """
    msg_fmt = ('An unknown exception occurred while trying to perform '
               'substitution. Details: %(details)s')
    code = 500
