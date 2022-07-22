import os


class Action(object):
    def __init__(
        self,
        icon=None,
        label=None,
        when=None,
        on_detail=False,
        on_list=False,
        url=None,
        confirm=None,
        name=None,
        view=None,
        method=None,
        **kwargs
    ):
        self.icon = icon
        self.label = label
        self.when = when
        self.url = url
        self.confirm = confirm
        self.method = method or 'get'
        # set during binding
        self.name = name
        self.view = view
        self.bound = bool(view)
        self.on_detail = on_detail
        self.on_list = on_list

    def serialize(self):
        return {
            "icon": self.icon,
            "label": self.label,
            "url": self.url,
            "confirm": self.confirm,
            "method": self.method,
            "name": self.name,
            "when": self.when,
        }

    def bind(self, view, name):
        """Create an action bound to one request"""
        url = self.url
        if not url:
            url = os.path.join(view.get_url(view.get_pk() or ":id"), name)
        elif callable(url):
            url = url(view)
        return Action(
            icon=self.icon,
            label=self.label,
            confirm=self.confirm,
            when=self.when,
            method=self.method,
            on_detail=self.on_detail,
            on_list=self.on_list,
            url=url,
            view=view,
            name=name,
        )


def action(**kwargs):
    a = Action(**kwargs)

    def decorator(fn):
        fn.drest_action = a
        return fn

    return decorator
