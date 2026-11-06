using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Shapes;

namespace DesktopSidecar;

/// <summary>绘制由独立透明窗口承载的代理指针与点击反馈，不操作系统鼠标。</summary>
internal sealed class AgentPointer : Canvas
{
    private const double DrawingHotspot = 48;
    private const double VisualScale = 0.8;
    internal const double Hotspot = DrawingHotspot * VisualScale;
    internal const double Extent = 144 * VisualScale;
    private readonly ScaleTransform ringScale = new(0.15, 0.15);
    private readonly ScaleTransform pointerScale = new(1, 1);
    private readonly Ellipse ring;

    /// <summary>缓存指针的冰白切面、反光、柔影和反馈层。</summary>
    internal AgentPointer()
    {
        Width = 144;
        Height = 144;
        HorizontalAlignment = HorizontalAlignment.Left;
        VerticalAlignment = VerticalAlignment.Top;
        IsHitTestVisible = false;
        LayoutTransform = new ScaleTransform(VisualScale, VisualScale);

        var haloBrush = new RadialGradientBrush();
        haloBrush.GradientStops.Add(new GradientStop(Color.FromArgb(48, 90, 176, 255), 0));
        haloBrush.GradientStops.Add(new GradientStop(Color.FromArgb(16, 72, 152, 255), 0.5));
        haloBrush.GradientStops.Add(new GradientStop(Color.FromArgb(0, 72, 152, 255), 1));
        haloBrush.Freeze();
        var halo = new Ellipse { Width = 78, Height = 78, Fill = haloBrush };
        SetLeft(halo, DrawingHotspot - 25);
        SetTop(halo, DrawingHotspot - 23);
        Children.Add(halo);

        ring = new Ellipse
        {
            Width = 92, Height = 92, Opacity = 0,
            Stroke = new SolidColorBrush(Color.FromRgb(129, 206, 255)), StrokeThickness = 1.5,
            Fill = new SolidColorBrush(Color.FromArgb(10, 93, 180, 255)),
            RenderTransform = ringScale, RenderTransformOrigin = new Point(0.5, 0.5),
        };
        SetLeft(ring, DrawingHotspot - 46);
        SetTop(ring, DrawingHotspot - 46);
        Children.Add(ring);

        var fill = new LinearGradientBrush { StartPoint = new Point(0.12, 0), EndPoint = new Point(0.92, 1) };
        fill.GradientStops.Add(new GradientStop(Color.FromRgb(255, 255, 255), 0));
        fill.GradientStops.Add(new GradientStop(Color.FromRgb(240, 250, 255), 0.40));
        fill.GradientStops.Add(new GradientStop(Color.FromRgb(178, 217, 249), 0.73));
        fill.GradientStops.Add(new GradientStop(Color.FromRgb(229, 245, 255), 1));
        fill.Freeze();
        Geometry silhouette = Geometry.Parse(
            "M 2,0 L 30,19 Q 31.4,20 29.6,20.4 L 20.4,22.1 " +
            "L 27.1,35.3 Q 27.7,36.5 26.5,37.1 L 22.1,39.3 " +
            "Q 20.9,39.9 20.3,38.7 L 13.5,25.3 L 6.9,32.1 " +
            "Q 5.6,33.4 5.2,31.4 L 0.5,1.5 Q 0.1,-0.8 2,0 Z");
        silhouette.Freeze();
        var glyph = new Grid { Width = 36, Height = 44, RenderTransform = pointerScale };
        var body = new Path
        {
            Data = silhouette, Fill = fill, Stroke = new SolidColorBrush(Color.FromRgb(119, 174, 217)),
            StrokeThickness = 0.85, StrokeLineJoin = PenLineJoin.Round,
            Effect = new DropShadowEffect
            {
                Color = Color.FromRgb(8, 24, 53), Opacity = 0.34,
                BlurRadius = 11, ShadowDepth = 3, Direction = 285,
                RenderingBias = RenderingBias.Quality,
            },
        };
        glyph.Children.Add(body);
        var bevel = new Path
        {
            Data = Geometry.Parse("M 2.8,3.0 L 26.4,18.8 L 18.5,20.4 M 2.8,3.0 L 6.8,28.3"),
            Stroke = new SolidColorBrush(Color.FromArgb(215, 255, 255, 255)), StrokeThickness = 1.05,
            StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round,
        };
        glyph.Children.Add(bevel);
        glyph.CacheMode = new BitmapCache(2);
        SetLeft(glyph, DrawingHotspot);
        SetTop(glyph, DrawingHotspot);
        Children.Add(glyph);
    }

    /// <summary>绘制一次向外扩散的轻光晕与按压回弹。</summary>
    internal void Pulse()
    {
        var expansion = new DoubleAnimation(0.12, 1, TimeSpan.FromMilliseconds(760))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
        };
        ringScale.BeginAnimation(ScaleTransform.ScaleXProperty, expansion);
        ringScale.BeginAnimation(ScaleTransform.ScaleYProperty, expansion);
        ring.BeginAnimation(OpacityProperty, new DoubleAnimation(0.75, 0, TimeSpan.FromMilliseconds(760)));
        var press = new DoubleAnimation(1, 0.92, TimeSpan.FromMilliseconds(110)) { AutoReverse = true };
        pointerScale.BeginAnimation(ScaleTransform.ScaleXProperty, press);
        pointerScale.BeginAnimation(ScaleTransform.ScaleYProperty, press);
    }

    /// <summary>隐藏时移除所有动画时钟，使下次显示从静止指针开始。</summary>
    internal void ResetFeedback()
    {
        ringScale.BeginAnimation(ScaleTransform.ScaleXProperty, null);
        ringScale.BeginAnimation(ScaleTransform.ScaleYProperty, null);
        ring.BeginAnimation(OpacityProperty, null);
        pointerScale.BeginAnimation(ScaleTransform.ScaleXProperty, null);
        pointerScale.BeginAnimation(ScaleTransform.ScaleYProperty, null);
    }
}
