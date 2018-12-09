import logging
import requests
from flask import redirect, url_for, Blueprint, flash, request, session
from flask_oauthlib.client import OAuth
from redash import models, settings
from redash.authentication import create_and_login_user, logout_and_redirect_to_index, get_next_path
from redash.authentication.org_resolving import current_org

logger = logging.getLogger('office365_oauth')

oauth = OAuth()
blueprint = Blueprint('office365_oauth', __name__)


def office365_remote_app():
    if 'office365' not in oauth.remote_apps:
        oauth.remote_app('office365',
                         base_url='https://login.microsoftonline.com/common',
                         authorize_url='https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
                         request_token_url=None,
                         request_token_params={
                             'scope': 'User.Read',
                         },
                         access_token_url='https://login.microsoftonline.com/common/oauth2/v2.0/token',
                         access_token_method='POST',
                         consumer_key=settings.OFFICE365_CLIENT_ID,
                         consumer_secret=settings.OFFICE365_CLIENT_SECRET)

    return oauth.office365


def get_user_profile(access_token):
    headers = {'Authorization': 'Bearer {}'.format(access_token)}
    response = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers)

    if response.status_code == 401:
        logger.warning("Failed getting user profile (response code 401).")
        return None

    return response.json()


def verify_profile(org, profile):
    if org.is_public:
        return True

    email = profile['mail']
    domain = email.split('@')[-1]

    if domain in org.office365_domains:
        return True

    if org.has_user(email) == 1:
        return True

    return False


@blueprint.route('/<org_slug>/oauth/office365', endpoint="authorize_org")
def org_login(org_slug):
    session['org_slug'] = current_org.slug
    return redirect(url_for(".authorize", next=request.args.get('next', None)))


@blueprint.route('/oauth/office365', endpoint="authorize")
def login():
    callback = url_for('.callback', _external=True)
    next_path = request.args.get('next', url_for("redash.index", org_slug=session.get('org_slug')))
    logger.debug("Callback url: %s", callback)
    logger.debug("Next is: %s", next_path)
    return office365_remote_app().authorize(callback=callback, state=next_path)


@blueprint.route('/auth/microsoft_v2_auth/callback', endpoint="callback")
def authorized():
    resp = office365_remote_app().authorized_response()
    access_token = resp['access_token']

    if access_token is None:
        logger.warning("Access token missing in call back request.")
        flash("Validation error. Please retry.")
        return redirect(url_for('redash.login'))

    profile = get_user_profile(access_token)
    if profile is None:
        flash("Validation error. Please retry.")
        return redirect(url_for('redash.login'))

    if 'org_slug' in session:
        org = models.Organization.get_by_slug(session.pop('org_slug'))
    else:
        org = current_org

    if not verify_profile(org, profile):
        logger.warning("User tried to login with unauthorized domain name: %s (org: %s)", profile['mail'], org)
        flash("Your Microsoft account ({}) isn't allowed.".format(profile['mail']))
        return redirect(url_for('redash.login', org_slug=org.slug))

    name = profile['mail']
    if 'givenName' in profile and 'surname' in profile:
        name = profile['givenName'] + ' ' + profile['surname']
    elif 'displayName' in profile:
        name = profile['displayName']

    user = create_and_login_user(org, name, profile['mail'])
    if user is None:
        return logout_and_redirect_to_index()

    unsafe_next_path = request.args.get('state') or url_for("redash.index", org_slug=org.slug)
    next_path = get_next_path(unsafe_next_path)

    return redirect(next_path)
