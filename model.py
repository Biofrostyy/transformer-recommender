"""
Alibaba transformer based recommender.

"""
import mxnet as mx
import numpy as np
from mxnet.gluon import nn
from mxnet.gluon.nn import HybridBlock, HybridSequential, LeakyReLU
from mxnet import gluon
import gluonnlp as nlp

# from gluonnlp.model.seq2seq_encoder_decoder import _get_attention_cell
# from gluonnlp.model.transformer import PositionwiseFFN
from transformer_blocks import _get_attention_cell, PositionwiseFFN

_SEQ_LEN = 32
_OTHER_LEN = 32

def _position_encoding_init(max_length, dim):
    """Init the sinusoid position encoding table """
    position_enc = np.arange(max_length).reshape((-1, 1)) \
                   / (np.power(10000, (2. / dim) * np.arange(dim).reshape((1, -1))))
    # print(position_enc)
    position_enc[:, 0::2] = np.sin(position_enc[:, 0::2])  # dim 2i
    position_enc[:, 1::2] = np.cos(position_enc[:, 1::2])

    return position_enc


class Rec(HybridBlock):
    def __init__(self, **kwargs):
        super(Rec, self).__init__(**kwargs)
        with self.name_scope():

            self.otherfeatures = nn.Embedding(input_dim=_OTHER_LEN,
                                              output_dim=32)
            self.features = HybridSequential()

            self.features.add(nn.Embedding(input_dim=_SEQ_LEN,
                                           output_dim=32)) # what dim?

            # Transformer

            # Multi-head attention with base cell scaled dot-product attention
            # Use b=1 self-attention blocks per article recommendation

            self.cell = _get_attention_cell('multi_head',
                                                      units=16,
                                                      scaled=True,
                                                      dropout=0.5,
                                                      num_heads=4,
                                                      use_bias=False,
                                            activation='relu')

            self.proj = nn.Dense(units=32,
                                 use_bias=False,
                                 bias_initializer='zeros',
                                 weight_initializer=None,
                                 flatten=False
                                 )
            self.drop_out_layer = nn.Dropout(rate=0.5)
            self.ffn = PositionwiseFFN(hidden_size=32,
                                        use_residual=True,
                                                             dropout=0.5,
                                                             units=32,
                                                             weight_initializer=None,
                                                             bias_initializer='zeros',
                                       activation='leakyrelu',
                                       ffn1_dropout=0.0
                                       )

            self.layer_norm = nn.LayerNorm(in_channels=32)

            # final MLP layers; dimensions were 1024, 512, 256
            self.output = HybridSequential()
            self.output.add(nn.Dense(8))
            self.output.add(LeakyReLU(alpha=0.1))
            self.output.add(nn.Dense(4))
            self.output.add(LeakyReLU(alpha=0.1))
            self.output.add(nn.Dense(2))
            self.output.add(LeakyReLU(alpha=0.1))
            self.output.add(nn.Dense(1))

    def _arange_like(self, F, inputs, axis):
        """Helper function to generate indices of a range"""
        if F == mx.ndarray:
            seq_len = inputs.shape[axis]
            arange = F.arange(seq_len, dtype=inputs.dtype, ctx=inputs.context)
        else:
            input_axis = inputs.slice(begin=(0, 0, 0), end=(1, None, 1)).reshape((-1))
            zeros = F.zeros_like(input_axis)
            arange = F.arange(start=0, repeat=1, step=1,
                              infer_range=True, dtype=inputs.dtype)
            arange = F.elemwise_add(arange, zeros)
            # print(arange)
        return arange


        # TODO: stack self attention blocks

            # TODO: clean up, replace hard_coded arguments and push online

            # TODO: change position encoding like in alibaba paper; likely the entire POsitionFFN class must be changed.
            # And check that the positional embedding gets added at the right spot for the input

            # TODO: c add other options (such as valid seq length and masking etc)



    def _get_positional(self, weight_type, max_length, units):
        if weight_type == 'sinusoidal':
            encoding = _position_encoding_init(max_length, units)

        # will have the alibaba weight_type as well

        return mx.nd.array(encoding)


    def hybrid_forward(self, F, x, x_other, mask=None):


        # The manual features
        x1 = self.otherfeatures(x_other)

        # The transformer features

        steps = self._arange_like(F, x, axis=1)
        x = self.features(x)
        position_weight = self._get_positional('sinusoidal', 32, 32)

        # add positional embedding
        positional_embedding = F.Embedding(steps, position_weight, 32, 32)
        #print(positional_embedding.shape)
        #print(x.shape)
        x = F.broadcast_add(x, F.expand_dims(positional_embedding, axis=0))

        # attention cell with dropout
        out_x, attn_w = self.cell(x, x, x, mask)

        out_x = self.proj(out_x)

        out_x = self.drop_out_layer(out_x)

        # add and norm

        out_x = x + out_x
        out_x = self.layer_norm(out_x)

        # ffn
        out_x = self.ffn(out_x)

        # concat other features with transformer representations
        # print(out_x.shape)
        # print(x1.shape)
        out_x = mx.ndarray.concat(out_x, x1)


        # Leakyrelu final layers
        out_x = self.output(out_x)

        return out_x


# Constructor
def ali_rec(**kwargs):
    net = Rec(**kwargs)
    return net