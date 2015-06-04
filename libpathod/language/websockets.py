import os
import netlib.websockets
import pyparsing as pp
from . import base, generators, actions, message


class WF(base.CaselessLiteral):
    TOK = "wf"


class OpCode(base.IntField):
    names = {
        "continue": netlib.websockets.OPCODE.CONTINUE,
        "text": netlib.websockets.OPCODE.TEXT,
        "binary": netlib.websockets.OPCODE.BINARY,
        "close": netlib.websockets.OPCODE.CLOSE,
        "ping": netlib.websockets.OPCODE.PING,
        "pong": netlib.websockets.OPCODE.PONG,
    }
    max = 15
    preamble = "c"


class Body(base.Value):
    preamble = "b"


class RawBody(base.Value):
    unique_name = "body"
    preamble = "r"


class Fin(base.Boolean):
    name = "fin"


class RSV1(base.Boolean):
    name = "rsv1"


class RSV2(base.Boolean):
    name = "rsv2"


class RSV3(base.Boolean):
    name = "rsv3"


class Mask(base.Boolean):
    name = "mask"


class Key(base.FixedLengthValue):
    preamble = "k"
    length = 4


class KeyNone(base.CaselessLiteral):
    unique_name = "key"
    TOK = "knone"


class Length(base.Integer):
    bounds = (0, 1 << 64)
    preamble = "l"


class Times(base.Integer):
    preamble = "x"


class NestedFrame(base.Token):
    def __init__(self, value):
        self.value = value
        try:
            self.parsed = WebsocketFrame(
                Response.expr().parseString(
                    value.val,
                    parseAll=True
                )
            )
        except pp.ParseException as v:
            raise exceptions.ParseException(v.msg, v.line, v.col)

    @classmethod
    def expr(klass):
        e = pp.Literal("wf").suppress()
        e = e + base.TokValueLiteral.expr()
        return e.setParseAction(lambda x: klass(*x))

    def values(self, settings):
        return [
            self.value.get_generator(settings),
        ]

    def spec(self):
        return "s%s" % (self.value.spec())

    def freeze(self, settings):
        f = self.parsed.freeze(settings).spec()
        return NestedFrame(base.TokValueLiteral(f.encode("string_escape")))


class WebsocketFrame(message.Message):
    comps = (
        OpCode,
        Length,
        # Bit flags
        Fin,
        RSV1,
        RSV2,
        RSV3,
        Mask,
        actions.PauseAt,
        actions.DisconnectAt,
        actions.InjectAt,
        KeyNone,
        Key,
        Times,

        Body,
        RawBody,
    )
    logattrs = ["body"]

    @property
    def actions(self):
        return self.toks(actions._Action)

    @property
    def body(self):
        return self.tok(Body)

    @property
    def rawbody(self):
        return self.tok(RawBody)

    @property
    def opcode(self):
        return self.tok(OpCode)

    @property
    def fin(self):
        return self.tok(Fin)

    @property
    def rsv1(self):
        return self.tok(RSV1)

    @property
    def rsv2(self):
        return self.tok(RSV2)

    @property
    def rsv3(self):
        return self.tok(RSV3)

    @property
    def mask(self):
        return self.tok(Mask)

    @property
    def key(self):
        return self.tok(Key)

    @property
    def knone(self):
        return self.tok(KeyNone)

    @property
    def times(self):
        return self.tok(Times)

    @property
    def toklength(self):
        return self.tok(Length)

    @classmethod
    def expr(klass):
        parts = [i.expr() for i in klass.comps]
        atom = pp.MatchFirst(parts)
        resp = pp.And(
            [
                WF.expr(),
                base.Sep,
                pp.ZeroOrMore(base.Sep + atom)
            ]
        )
        resp = resp.setParseAction(klass)
        return resp

    def resolve(self, settings, msg=None):
        tokens = self.tokens[:]
        if not self.mask and settings.is_client:
            tokens.append(
                Mask(True)
            )
        if not self.knone and self.mask and self.mask.value and not self.key:
            tokens.append(
                Key(base.TokValueLiteral(os.urandom(4)))
            )
        return self.__class__(
            [i.resolve(settings, self) for i in tokens]
        )

    def values(self, settings):
        if self.body:
            bodygen = self.body.value.get_generator(settings)
            length = len(self.body.value.get_generator(settings))
        elif self.rawbody:
            bodygen = self.rawbody.value.get_generator(settings)
            length = len(self.rawbody.value.get_generator(settings))
        else:
            bodygen = None
            length = 0
        if self.toklength:
            length = int(self.toklength.value)
        frameparts = dict(
            payload_length = length
        )
        if self.mask and self.mask.value:
            frameparts["mask"] = True
        if self.knone:
            frameparts["masking_key"] = None
        elif self.key:
            key = self.key.values(settings)[0][:]
            frameparts["masking_key"] = key
        for i in ["opcode", "fin", "rsv1", "rsv2", "rsv3", "mask"]:
            v = getattr(self, i, None)
            if v is not None:
                frameparts[i] = v.value
        frame = netlib.websockets.FrameHeader(**frameparts)
        vals = [frame.to_bytes()]
        if bodygen:
            if frame.masking_key and not self.rawbody:
                masker = netlib.websockets.Masker(frame.masking_key)
                vals.append(
                    generators.TransformGenerator(
                        bodygen,
                        masker.mask
                    )
                )
            else:
                vals.append(bodygen)
        return vals

    def spec(self):
        return ":".join([i.spec() for i in self.tokens])
