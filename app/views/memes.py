import asyncio

from sanic import Blueprint, response
from sanic.log import logger
from sanic_openapi import doc

from .. import helpers, models, settings, utils
from .templates import generate_url

blueprint = Blueprint("Memes", url_prefix="/images")


@blueprint.get("/")
@doc.summary("List example memes")
@doc.operation("Memes.list")
@doc.consumes(
    doc.String(
        name="filter", description="Part of the template name or example to match"
    ),
    location="query",
)
@doc.produces(
    doc.List({"url": str, "template": str}),
    description="Successfully returned a list of example memes",
    content_type="application/json",
)
async def index(request):
    query = request.args.get("filter", "").lower()
    examples = await asyncio.to_thread(helpers.get_example_images, request, query)
    return response.json(
        [{"url": url, "template": template} for url, template in examples]
    )


@blueprint.post("/")
@doc.summary("Create a meme from a template")
@doc.operation("Memes.create")
@doc.consumes(
    doc.JsonBody(
        {
            "template_id": str,
            "text_lines": [str],
            "style": [str],
            "extension": str,
            "redirect": bool,
        }
    ),
    content_type="application/json",
    location="body",
)
@doc.response(201, {"url": str}, description="Successfully created a meme")
@doc.response(
    400, {"error": str}, description='Required "template_id" missing in request body'
)
@doc.response(404, {"error": str}, description='Specified "template_id" does not exist')
async def create(request):
    return await generate_url(request, template_id_required=True)


@blueprint.post("/automatic")
@doc.exclude(not settings.REMOTE_TRACKING_URL)
@doc.summary("Create a meme from word or phrase")
@doc.consumes(
    doc.JsonBody({"text": str, "safe": bool, "redirect": bool}),
    content_type="application/json",
    location="body",
)
@doc.response(201, {"url": str}, description="Successfully created a meme")
@doc.response(
    400, {"error": str}, description='Required "text" missing in request body'
)
async def automatic(request):
    if request.form:
        payload = dict(request.form)
    else:
        payload = request.json or {}

    try:
        query = payload["text"]
    except KeyError:
        return response.json({"error": '"text" is required'}, status=400)

    results = await utils.meta.search(request, query, payload.get("safe", True))
    logger.info(f"Found {len(results)} result(s)")
    if not results:
        return response.json({"message": f"No results matched: {query}"}, status=404)

    url = utils.urls.normalize(results[0]["image_url"])
    confidence = results[0]["confidence"]
    logger.info(f"Top result: {url} ({confidence})")
    url, _updated = await utils.meta.tokenize(request, url)

    if payload.get("redirect", False):
        return response.redirect(utils.urls.add(url, status="201"))

    return response.json({"url": url, "confidence": confidence}, status=201)


@blueprint.post("/custom")
@doc.summary("Create a meme from any image")
@doc.consumes(
    doc.JsonBody(
        {
            "background": str,
            "style": str,
            "text_lines": [str],
            "extension": str,
            "redirect": bool,
        }
    ),
    content_type="application/json",
    location="body",
)
@doc.response(
    201, {"url": str}, description="Successfully created a meme from a custom image"
)
async def custom(request):
    return await generate_url(request)


@blueprint.get("/custom")
@doc.summary("List popular custom memes")
@doc.operation("Memes.list_custom")
@doc.consumes(
    doc.Boolean(name="safe", description="Exclude NSFW results"),
    content_type="application/json",
    location="query",
)
@doc.consumes(
    doc.String(name="filter", description="Part of the meme's text to match"),
    location="query",
)
@doc.produces(
    doc.List({"url": str}),
    description="Successfully returned a list of custom memes",
    content_type="application/json",
)
async def list_custom(request):
    query = request.args.get("filter", "").lower()
    safe = utils.urls.flag(request, "safe", True)

    results = await utils.meta.search(request, query, safe, mode="results")
    logger.info(f"Found {len(results)} result(s)")
    if not results:
        return response.json({"message": f"No results matched: {query}"}, status=404)

    items = []
    for result in results:
        url = utils.urls.normalize(result["image_url"])
        url, _updated = await utils.meta.tokenize(request, url)
        items.append({"url": url})

    return response.json(items, status=200)


@blueprint.get(r"/<template_id:.+\.\w+>")
@doc.tag("Templates")
@doc.summary("Display a template background")
@doc.consumes(doc.String(name="template_id"), location="path")
@doc.produces(
    doc.File(),
    description="Successfully displayed a template background",
    content_type="image/*",
)
@doc.response(404, doc.File(), description="Template not found")
@doc.response(415, doc.File(), description="Unable to download image URL")
@doc.response(
    422,
    doc.File(),
    description="Invalid style for template or no image URL specified for custom template",
)
async def blank(request, template_id):
    template_id, extension = template_id.rsplit(".", 1)

    if request.args.get("style") == "animated" and extension != "gif":
        # TODO: Move this pattern to utils
        params = {k: v for k, v in request.args.items() if k != "style"}
        url = request.app.url_for(
            "Memes.blank",
            template_id=template_id + ".gif",
            **params,
        )
        return response.redirect(utils.urls.clean(url), status=301)

    return await render_image(request, template_id, extension=extension)


@blueprint.get(r"/<template_id:slug>/<text_paths:[^/].*\.\w+>")
@doc.summary("Display a custom meme")
@doc.consumes(doc.String(name="text_paths"), location="path")
@doc.consumes(doc.String(name="template_id"), location="path")
@doc.produces(
    doc.File(),
    description="Successfully displayed a custom meme",
    content_type="image/*",
)
@doc.response(404, doc.File(), description="Template not found")
@doc.response(414, doc.File(), description="Custom text too long (length >200)")
@doc.response(415, doc.File(), description="Unable to download image URL")
@doc.response(
    422,
    doc.File(),
    description="Invalid style for template or no image URL specified for custom template",
)
async def text(request, template_id, text_paths):
    text_paths, extension = text_paths.rsplit(".", 1)

    if request.args.get("style") == "animated" and extension != "gif":
        # TODO: Move this pattern to utils
        params = {k: v for k, v in request.args.items() if k != "style"}
        url = request.app.url_for(
            "Memes.text",
            template_id=template_id,
            text_paths=text_paths + ".gif",
            **params,
        )
        return response.redirect(utils.urls.clean(url), status=301)

    slug, updated = utils.text.normalize(text_paths)
    if updated:
        url = request.app.url_for(
            "Memes.text",
            template_id=template_id,
            text_paths=slug + "." + extension,
            **request.args,
        )
        return response.redirect(utils.urls.clean(url), status=301)

    url, updated = await utils.meta.tokenize(request, request.url)
    if updated:
        return response.redirect(url, status=302)

    watermark, updated = await utils.meta.get_watermark(request)
    if updated:
        # TODO: Move this pattern to utils
        params = {k: v for k, v in request.args.items() if k != "watermark"}
        url = request.app.url_for(
            "Memes.text",
            template_id=template_id,
            text_paths=slug + "." + extension,
            **params,
        )
        return response.redirect(utils.urls.clean(url), status=302)

    return await render_image(request, template_id, slug, watermark, extension)


async def render_image(
    request,
    id: str,
    slug: str = "",
    watermark: str = "",
    extension: str = settings.DEFAULT_EXTENSION,
):
    lines = utils.text.decode(slug)
    asyncio.create_task(utils.meta.track(request, lines))

    status = int(utils.urls.arg(request.args, "200", "status"))

    if any(len(part.encode()) > 200 for part in slug.split("/")):
        logger.error(f"Slug too long: {slug}")
        slug = slug[:50] + "..."
        lines = utils.text.decode(slug)
        template = models.Template.objects.get("_error")
        style = settings.DEFAULT_STYLE
        status = 414

    elif id == "custom":
        url = utils.urls.arg(request.args, None, "background", "alt")
        if url:
            template = await models.Template.create(url)
            if not template.image.exists():
                logger.error(f"Unable to download image URL: {url}")
                template = models.Template.objects.get("_error")
                if url != settings.PLACEHOLDER:
                    status = 415

            style = utils.urls.arg(request.args, settings.DEFAULT_STYLE, "style")
            if not utils.urls.schema(style):
                style = style.lower()
            if not await template.check(style):
                if utils.urls.schema(style):
                    status = 415
                elif style != settings.PLACEHOLDER:
                    status = 422

        else:
            logger.error("No image URL specified for custom template")
            template = models.Template.objects.get("_error")
            style = settings.DEFAULT_STYLE
            status = 422

    else:
        template = models.Template.objects.get_or_none(id)
        if not template or not template.image.exists():
            logger.error(f"No such template: {id}")
            template = models.Template.objects.get("_error")
            if id != settings.PLACEHOLDER:
                status = 404

        style = utils.urls.arg(request.args, settings.DEFAULT_STYLE, "style", "alt")
        if not await template.check(style):
            if utils.urls.schema(style):
                status = 415
            elif style != settings.PLACEHOLDER:
                status = 422

    try:
        size = int(request.args.get("width", 0)), int(request.args.get("height", 0))
        if 0 < size[0] < 10 or 0 < size[1] < 10:
            raise ValueError(f"dimensions are too small: {size}")
    except ValueError as e:
        logger.error(f"Invalid size: {e}")
        size = 0, 0
        status = 422

    if extension not in settings.ALLOWED_EXTENSIONS:
        extension = settings.DEFAULT_EXTENSION
        status = 422

    path = await asyncio.to_thread(
        utils.images.save,
        template,
        lines,
        watermark,
        extension=extension,
        style=style,
        size=size,
    )
    return await response.file(path, status)
