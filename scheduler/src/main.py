#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

import os
import logging

from flask import Flask
from flask_cors import CORS

from routes import (
    auth,
    users,
    errors,
    channels,
    orders,
    tasks,
    home,
    warehouses,
    workers,
    autoimages,
    stripe,
)
from utils.json import Encoder
from utils.templates import strftime
from prestart import Initializer

logging.basicConfig(level=logging.INFO)

flask = Flask(__name__)
flask.json_encoder = Encoder
flask.jinja_env.filters["date"] = strftime
CORS(flask)

flask.register_blueprint(home.blueprint)
flask.register_blueprint(auth.blueprint)
flask.register_blueprint(users.blueprint)
flask.register_blueprint(channels.blueprint)
flask.register_blueprint(orders.blueprint)
flask.register_blueprint(tasks.blueprint)
flask.register_blueprint(warehouses.blueprint)
flask.register_blueprint(workers.blueprint)
flask.register_blueprint(autoimages.blueprint)
flask.register_blueprint(stripe.blueprint)

errors.register_handlers(flask)


if __name__ == "__main__":
    Initializer.start()

    is_debug = os.getenv("DEBUG", False)
    flask.run(host="0.0.0.0", debug=is_debug, port=80, threaded=True)
