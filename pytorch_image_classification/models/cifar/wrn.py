import torch
import torch.nn as nn
import torch.nn.functional as F

from ..initializer import create_initializer


class BasicBlock(nn.Module):
    def __init__(self, in_channels, out_channels, stride, drop_rate):
        super().__init__()

        self.drop_rate = drop_rate

        self._preactivate_both = (in_channels != out_channels)

        self.bn1 = nn.BatchNorm2d(in_channels)
        self.conv1 = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size=3,
            stride=stride,  # downsample with first conv
            padding=1,
            bias=False)

        self.bn2 = nn.BatchNorm2d(out_channels)
        self.conv2 = nn.Conv2d(out_channels,
                               out_channels,
                               kernel_size=3,
                               stride=1,
                               padding=1,
                               bias=False)

        self.shortcut = nn.Sequential()
        if in_channels != out_channels:
            self.shortcut.add_module(
                'conv',
                nn.Conv2d(
                    in_channels,
                    out_channels,
                    kernel_size=1,
                    stride=stride,  # downsample
                    padding=0,
                    bias=False))

    def forward(self, x):
        if self._preactivate_both:
            x = F.relu(self.bn1(x),
                       inplace=True)  # shortcut after preactivation
            y = self.conv1(x)
        else:
            y = F.relu(self.bn1(x),
                       inplace=True)  # preactivation only for residual path
            y = self.conv1(y)
        if self.drop_rate > 0:
            y = F.dropout(y,
                          p=self.drop_rate,
                          training=self.training,
                          inplace=False)

        y = F.relu(self.bn2(y), inplace=True)
        y = self.conv2(y)
        y += self.shortcut(x)
        return y


class Network(nn.Module):
    def __init__(self, config):
        super().__init__()

        model_config = config.model.wrn
        depth = model_config.depth
        initial_channels = model_config.initial_channels
        widening_factor = model_config.widening_factor
        drop_rate = model_config.drop_rate

        block = BasicBlock
        n_blocks_per_stage = (depth - 4) // 6
        assert n_blocks_per_stage * 6 + 4 == depth

        n_channels = [
            initial_channels,
            initial_channels * widening_factor,
            initial_channels * 2 * widening_factor,
            initial_channels * 4 * widening_factor,
        ]

        self.conv = nn.Conv2d(config.dataset.n_channels,
                              n_channels[0],
                              kernel_size=3,
                              stride=1,
                              padding=1,
                              bias=False)

        self.stage1 = self._make_stage(n_channels[0],
                                       n_channels[1],
                                       n_blocks_per_stage,
                                       block,
                                       stride=1,
                                       drop_rate=drop_rate)
        self.stage2 = self._make_stage(n_channels[1],
                                       n_channels[2],
                                       n_blocks_per_stage,
                                       block,
                                       stride=2,
                                       drop_rate=drop_rate)
        self.stage3 = self._make_stage(n_channels[2],
                                       n_channels[3],
                                       n_blocks_per_stage,
                                       block,
                                       stride=2,
                                       drop_rate=drop_rate)
        self.bn = nn.BatchNorm2d(n_channels[3])

        # compute conv feature size
        with torch.no_grad():
            dummy_data = torch.zeros(
                (1, config.dataset.n_channels, config.dataset.image_size,
                 config.dataset.image_size),
                dtype=torch.float32)
            self.feature_size = self._forward_conv(dummy_data).view(
                -1).shape[0]

        self.fc = nn.Linear(self.feature_size, config.dataset.n_classes)

        # initialize weights
        initializer = create_initializer(config.model.init_mode)
        self.apply(initializer)

    def _make_stage(self, in_channels, out_channels, n_blocks, block, stride,
                    drop_rate):
        stage = nn.Sequential()
        for index in range(n_blocks):
            block_name = f'block{index + 1}'
            if index == 0:
                stage.add_module(
                    block_name,
                    block(in_channels,
                          out_channels,
                          stride=stride,
                          drop_rate=drop_rate))
            else:
                stage.add_module(
                    block_name,
                    block(out_channels,
                          out_channels,
                          stride=1,
                          drop_rate=drop_rate))
        return stage

    def _forward_conv(self, x):
        x = self.conv(x)
        x = self.stage1(x)
        x = self.stage2(x)
        x = self.stage3(x)
        x = F.relu(self.bn(x), inplace=True)
        x = F.adaptive_avg_pool2d(x, output_size=1)
        return x

    def forward(self, x):
        x = self._forward_conv(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x
