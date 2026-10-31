import logging

from flask import jsonify

from .exceptions import AppError


def register_error_handlers(app):
    logger = logging.getLogger("nsp")

    @app.errorhandler(AppError)
    def handle_app_error(err):  # noqa: ANN001
        if err.status_code >= 500:
            logger.error("AppError %s: %s", err.status_code, err.message)
        elif err.status_code >= 400 and err.status_code < 500:
            logger.info("AppError %s: %s", err.status_code, err.message)
        else:
            logger.debug("AppError %s: %s", err.status_code, err.message)
        response = jsonify(err.to_dict())
        response.status_code = err.status_code
        return response

    @app.errorhandler(400)
    def handle_400(err):
        return jsonify({"error": "BadRequest", "message": "Invalid request"}), 400

    @app.errorhandler(404)
    def handle_404(err):
        return jsonify({"error": "NotFound", "message": "Resource not found"}), 404

    @app.errorhandler(405)
    def handle_405(err):
        return jsonify({"error": "MethodNotAllowed", "message": "Method not allowed"}), 405

    @app.errorhandler(429)
    def handle_429(err):
        return jsonify({"error": "TooManyRequests", "message": "Rate limit exceeded"}), 429

    @app.errorhandler(500)
    def handle_500(err):  # noqa: ANN001
        logger.exception("Unhandled server error")
        return jsonify({"error": "InternalServerError", "message": "Internal server error"}), 500
