import torch
import torch.nn as nn
import torch.nn.functional as F


class AdaptiveConv2d(nn.Module):
    """
    Adaptive convolutional layer, takes an additional input which forms the convolution kernel and then applies it
    to the primary input. In this case the filter manifold network which generates the kernel is fixed
    to a size of 3 layers which double in the number of output neurons until the final output dimension is
    reached.
    This layer type is based on:
    "Incorporating Side Information by Adaptive Convolution", Kang et al., NIPS 2017

    The implementation with exploiting grouped convolutions is based on this answer on StackOverflow:
    https://stackoverflow.com/questions/42068999/tensorflow-convolutions-with-different-filter-for-each-sample-in-the-mini-batch

    Further relevant links to understand the implementation:
    https://discuss.pytorch.org/t/conv2d-certain-values-for-groups-and-out-channels-dont-work/14228
    https://github.com/pytorch/pytorch/issues/3653

    Additional Notes:
    Currently only supports same padding. 

    Parameters:
    channels_in - 2D input tensor
    channels_out - 2D output tensor
    features_in - 1D input tensor, the adaptive features
    kernel_size - scalar or tuple, if tuple its assumed to be of quadratic shape, e.g. (3,3) and not (3,2)
    manifold_network - defines number of neurons and layers of the internal filter manifold network

    Example:
        >>> batch_size = 4
        >>> ada_conv = AdaptiveConv2d(channels_in=8, channels_out=16, features_in=2, kernel_size=(3,3))
        >>> x = torch.rand(batch_size, 8, 120, 120)
        >>> z = torch.rand(batch_size, 2)
        >>> out = ada_conv(x, z)
    """

    def __init__(self, channels_in, channels_out, features_in, kernel_size, manifold_network_definition=[20, 30]):
        super(AdaptiveConv2d, self).__init__()

        # convert kernel tuple to scalar
        if type(kernel_size) == tuple:
            assert(len(kernel_size) == 2)
            assert(kernel_size[0] == kernel_size[1])
            kernel_size = kernel_size[0]

        self.features_in = features_in
        self.channels_in = channels_in
        self.channels_out = channels_out
        self.kernel_size = kernel_size
        self.same_padding = self.kernel_size // 2

        # represents the filter manifold network from the paper
        self.num_kernel_weights = channels_in * channels_out * kernel_size**2
        self.num_bias_weights = channels_out
        num_neurons_out = self.num_kernel_weights + self.num_bias_weights # last sum for bias

        # define the filter manifold network
        self.manifold_network = [nn.Linear(features_in, manifold_network_definition[0])]
        for in_features, out_features in [x for x in zip(manifold_network_definition[:-1], manifold_network_definition[1:])]:
            self.manifold_network.append(nn.Linear(in_features, out_features))
        self.manifold_network.append(nn.Linear(manifold_network_definition[-1], num_neurons_out))


    def forward(self, x, z):
        """
        Takes a tensor x and an auxillary input z. This is implemented as a hack, exploiting
        grouped convolutions to process a whole input batch at once while applying multiple convolutions
        (channels and kernels) to each batch-instance without a loop.
        """
        # run filter manifold network to get convolution weights
        for layer in self.manifold_network:
            z = F.relu(layer(z))
        adaptive_weights = z

        # reshape to enable batch processing
        batch_size, x_c_in, x_h, x_w = x.shape
        x = x.view(1, batch_size * x_c_in, x_h, x_w)

        # initialize convolution only here as weights are assigned new at each pass
        conv = nn.Conv2d(batch_size*self.channels_in, batch_size*self.channels_out, self.kernel_size, \
                        padding=self.same_padding, groups=batch_size)

        # assign computed weights to convolution
        conv_kernel_weights = adaptive_weights[:,:self.num_kernel_weights].contiguous().view(batch_size*self.channels_out, self.channels_in, self.kernel_size, self.kernel_size)
        conv_bias_weights = adaptive_weights[:,-self.num_bias_weights:].contiguous().view(-1) # be aware of contiguous (problematic?)
        conv._parameters['weight'] = conv_kernel_weights
        conv._parameters['bias'] = conv_bias_weights

        # apply convolution on input and reshape to desired output
        x = F.relu(conv(x))
        x = x.view(-1, self.channels_out, x_h, x_w)
        return x