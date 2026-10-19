"""The owner's own profile picture: validated, revisioned, and never readable by an agent."""

import base64

import pytest
from test_database_setup import database as database_fixture
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import profile_images
from space.profile_images import SaveImage
from space.security.auth import Unauthenticated
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic picture"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 synthetic"


def data_url(mime, raw):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def test_a_picture_is_saved_replaced_and_removed(commands):
    service, owner, _, connect = commands
    assert profile_images.get(service, owner) == {"image": None}

    first = data_url("image/png", PNG)
    assert profile_images.save(service, owner, SaveImage(image=first)) == {"image": first}
    assert profile_images.get(service, owner) == {"image": first}
    with connect() as connection:
        stored = connection.execute("SELECT revision FROM space.profile_images").fetchall()
    assert [revision for (revision,) in stored] == [1]

    second = data_url("image/webp", WEBP)
    profile_images.save(service, owner, SaveImage(image=second))
    assert profile_images.get(service, owner) == {"image": second}
    assert profile_images.delete(service, owner) == {"image": None}
    assert profile_images.get(service, owner) == {"image": None}


def test_only_real_small_pictures_are_accepted(commands):
    service, owner, _, _ = commands
    for image, message in (
        ("https://images.invalid/me.png", "PNG, JPEG or WebP"),
        (data_url("image/gif", b"GIF89a"), "PNG, JPEG or WebP"),
        (data_url("image/png", b"not a picture"), "does not match"),
        (data_url("image/jpeg", b"\xff\xd8\xff" + b"0" * profile_images.MAX_IMAGE_BYTES), "200 KB"),
    ):
        with pytest.raises((CommandError, ValueError), match=message):
            profile_images.save(service, owner, SaveImage(image=image))
    assert profile_images.get(service, owner) == {"image": None}


def test_agents_never_read_or_change_the_owners_picture(commands):
    service, owner, _, _ = commands
    profile_images.save(service, owner, SaveImage(image=data_url("image/png", PNG)))
    agent = delegated(service, owner, write=True)
    with pytest.raises((CommandError, Unauthenticated)):
        profile_images.get(service, agent)
    with pytest.raises((CommandError, Unauthenticated)):
        profile_images.save(service, agent, SaveImage(image=data_url("image/png", PNG)))
