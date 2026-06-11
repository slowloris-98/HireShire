class SlugNotFoundError(Exception):
    def __init__(self, platform: str, token: str):
        self.platform = platform
        self.token = token
        super().__init__(f"{platform}: slug {token!r} not found")
